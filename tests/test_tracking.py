"""Tejas's journey end to end through the API, plus tracking rules."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agripulse_api.models import Forecast, GeofenceEvent, Mandi, Organization, Trip, User, Vehicle
from tracking.engine import TrackingNotActive, process_points
from tracking.monitor import check_stale_trips

FARM = (13.20, 78.02)  # a tomato farm west of Kolar


def seed_forecasts(db, spike=0.2):
    today = date.today()
    for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None))):
        base = 1500 + (m.id * 97) % 900
        for h in (1, 2, 3, 4):
            db.add(Forecast(mandi_id=m.id, commodity="Tomato", issue_date=today, target_date=today + timedelta(weeks=h),
                            horizon_weeks=h, p10=base * 0.8, p50=base, p90=base * 1.3, spike_prob=spike,
                            model_name="lightgbm_quantile", model_version="test", trained_on_synthetic=True))
    db.commit()


def org_id(db, name):
    return db.scalar(select(Organization.id).where(Organization.name == name))


def path(a, b, n):
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


@pytest.fixture()
def journey(client, as_role, db):
    """Runs the story up to 'trip started + pickup scanned' and returns the ids."""
    seed_forecasts(db)
    fpo_org = org_id(db, "Kolar Tomato Growers FPO (demo)")
    lender_org = org_id(db, "Grama Credit Co-op (demo)")
    fleet_org = org_id(db, "Hebbal Haulage (demo)")
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))

    lot = client.post("/lots", headers=as_role("farmer"), json={
        "quantity_tons": 2, "grade": "Local", "pickup_label": "Tejas farm, Vemagal",
        "pickup_lat": FARM[0], "pickup_lon": FARM[1], "fpo_org_id": fpo_org, "lender_org_id": lender_org}).json()

    rec = client.get("/recommend/best-mandi", params={"lot_id": lot["id"]}, headers=as_role("farmer")).json()
    sh = client.post("/shipments", headers=as_role("fpo"), json={"mandi_id": kolar.id, "lot_ids": [lot["id"]]}).json()
    client.post(f"/shipments/{sh['id']}/book", headers=as_role("fpo"), json={"fleet_org_id": fleet_org})
    vehicle = db.scalar(select(Vehicle).where(Vehicle.registration == "KA-01-XX-1234"))
    driver = db.scalar(select(User).where(User.email == "driver@demo.agripulse"))
    trip = client.post("/trips", headers=as_role("fleet_owner"),
                       json={"shipment_id": sh["id"], "vehicle_id": vehicle.id, "driver_id": driver.id})
    assert trip.status_code == 201, trip.text
    trip = trip.json()
    return {"lot": lot, "rec": rec, "shipment": sh, "trip": trip, "kolar": kolar}


def test_tejas_journey(client, as_role, db, journey):
    lot, trip, kolar = journey["lot"], journey["trip"], journey["kolar"]
    D = as_role("driver")

    # Best mandi: ranked by net value, with a range, never a single number
    rec = journey["rec"]
    assert rec["ranked"] and rec["ranked"][0]["rank"] == 1
    top = rec["ranked"][0]["net_value"]
    assert top["p10"] <= top["p50"] <= top["p90"]
    assert rec["ranked"][0]["net_value"]["p50"] >= rec["ranked"][-1]["net_value"]["p50"]

    # Driver lifecycle: accept -> consent required -> start
    assert client.post(f"/trips/{trip['id']}/accept", headers=D).status_code == 200
    assert client.post(f"/trips/{trip['id']}/start", headers=D).status_code == 409  # no consent yet
    assert client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": [
        {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": FARM[0], "lon": FARM[1]}]}).status_code == 409
    client.post(f"/trips/{trip['id']}/consent", headers=D, json={"consent": True})
    started = client.post(f"/trips/{trip['id']}/start", headers=D).json()
    assert started["status"] == "in_progress" and started["tracking_on"]

    # QR at pickup: the token is on the farmer's screen, the driver scans it
    farmer_view = client.get(f"/lots/{lot['id']}", headers=as_role("farmer")).json()
    token = farmer_view["trip"]["pickup_qr_token"]
    assert client.post(f"/trips/{trip['id']}/scan/pickup", headers=D, json={"token": "wrong"}).status_code == 400
    assert client.post(f"/trips/{trip['id']}/scan/pickup", headers=D, json={"token": token}).status_code == 200
    assert client.get(f"/lots/{lot['id']}", headers=as_role("farmer")).json()["status"] == "in_transit"
    alerts = client.get("/alerts", headers=as_role("farmer")).json()
    assert any(a["kind"] == "picked_up" and "/track/" in a["body"] for a in alerts)

    # GPS: farm -> Kolar APMC, timestamps within the last minute
    t0 = datetime.now(timezone.utc) - timedelta(seconds=50)
    pts = [{"recorded_at": (t0 + timedelta(seconds=i)).isoformat(), "lat": la, "lon": lo, "speed_kmph": 40}
           for i, (la, lo) in enumerate(path(FARM, (kolar.lat, kolar.lon), 40))]
    first = client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": pts[:20]}).json()
    assert first["accepted"] == 20 and "left_pickup_zone" in first["events"]
    mid = client.get(f"/trips/{trip['id']}", headers=as_role("farmer")).json()
    assert mid["remaining_km"] > 0 and mid["eta_at"] is not None and mid["vehicle"] == "KA-01-XX-1234"

    # Offline buffer replay: re-sending points is harmless
    replay = client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": pts[:25]}).json()
    assert replay["duplicates"] == 20 and replay["accepted"] == 5
    rest = client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": pts[25:]}).json()
    assert "reached_mandi" in rest["events"]
    assert any(a["kind"] == "vehicle_arrived" for a in client.get("/alerts", headers=as_role("trader")).json())

    # Public link: no login, only position / ETA / lot status
    share = client.get(f"/trips/{trip['id']}", headers=as_role("farmer")).json()["share_url"]
    pub = client.get("/public/track/" + share.rsplit("/", 1)[1])
    assert pub.status_code == 200
    assert set(pub.json()) == {"status", "lat", "lon", "last_seen_at", "remaining_km", "eta_at", "eta_local", "lots", "is_simulated"}
    assert client.get("/public/track/not-a-real-token").status_code == 404

    # Trader: incoming board, QR at delivery (token on the driver's phone), weigh + price
    board = client.get("/trader/board", headers=as_role("trader")).json()
    assert any(i["trip_id"] == trip["id"] and i["tons"] == 2 for i in board["incoming"])
    delivery_token = client.get(f"/trips/{trip['id']}", headers=D).json()["delivery_qr_token"]
    assert client.post(f"/trips/{trip['id']}/scan/delivery", headers=as_role("trader"), json={"token": delivery_token}).status_code == 200
    w = client.post(f"/trader/lots/{lot['id']}/weigh", headers=as_role("trader"), json={"weight_kg": 1985, "price_per_quintal": 1400})
    assert w.status_code == 200 and w.json()["shipment_status"] == "delivered"
    delivered = [a for a in client.get("/alerts", headers=as_role("farmer")).json() if a["kind"] == "delivered"]
    assert delivered and "1985 kg" in delivered[0]["body"]

    # Driver ends the trip -> tracking stops
    assert client.post(f"/trips/{trip['id']}/end", headers=D).json()["tracking_on"] is False
    late = client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": [
        {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": kolar.lat, "lon": kolar.lon}]})
    assert late.status_code == 409

    # FPO: farmer-wise tonnage + payout; lender: verified chain + reliability
    sh = client.get(f"/shipments/{journey['shipment']['id']}", headers=as_role("fpo")).json()
    assert sh["farmers"][0]["tons"] == 2 and sh["farmers"][0]["delivered_kg"] == 1985
    assert client.post(f"/lots/{lot['id']}/payout", headers=as_role("fpo")).json()["payout_status"] == "paid"
    lender = client.get("/lender/lots", headers=as_role("lender")).json()
    entry = lender["lots"][0]
    assert entry["delivery"]["weight_kg"] == 1985 and entry["reliability"]["checks"]["pickup_qr"]
    assert entry["reliability"]["checks"]["delivery_qr"] and entry["delivery"]["value_rs"] == round(19.85 * 1400)


def test_tenant_isolation(client, as_role, db, journey):
    trip_id, lot_id = journey["trip"]["id"], journey["lot"]["id"]
    # a second farmer, a second FPO and a second lender see none of it
    other = client.post("/auth/register", json={"email": "other@x.in", "password": "longenough", "full_name": "Other", "role": "farmer"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert client.get(f"/lots/{lot_id}", headers=oh).status_code == 404
    assert client.get(f"/trips/{trip_id}", headers=oh).status_code == 404
    fpo2 = client.post("/auth/register", json={"email": "fpo2@x.in", "password": "longenough", "full_name": "F2",
                                                "role": "fpo", "org_name": "Another FPO"})
    fh = {"Authorization": f"Bearer {fpo2.json()['access_token']}"}
    assert client.get("/lots", headers=fh).json() == []
    assert client.get(f"/shipments/{journey['shipment']['id']}", headers=fh).status_code == 404
    l2 = client.post("/auth/register", json={"email": "l2@x.in", "password": "longenough", "full_name": "L2",
                                              "role": "lender", "org_name": "Other Bank"})
    assert client.get("/lender/lots", headers={"Authorization": f"Bearer {l2.json()['access_token']}"}).json()["lots"] == []
    # driver can't drive someone else's trip; buyer/policy can't see raw trips
    assert client.get(f"/trips/{trip_id}", headers=as_role("buyer")).status_code == 403
    # FPOs cannot assign vehicles, only book a fleet
    assert client.post("/trips", headers=as_role("fpo"), json={"shipment_id": 1, "vehicle_id": 1, "driver_id": 1}).status_code == 403


def _start(db, trip_id, minutes_ago):
    t = db.get(Trip, trip_id)
    now = datetime.now(timezone.utc)
    t.status, t.consent_given_at, t.started_at = "in_progress", now, now - timedelta(minutes=minutes_ago)
    db.commit()
    return t


def test_unexpected_stop_and_delay(client, db, journey):
    t = _start(db, journey["trip"]["id"], 120)
    now = datetime.now(timezone.utc)
    mid = (13.17, 78.07)  # on the road, away from farm and mandi
    pts = [{"recorded_at": now - timedelta(minutes=40) + timedelta(minutes=i), "lat": mid[0], "lon": mid[1], "speed_kmph": 0}
           for i in range(40)]
    out = process_points(db, t, pts, now=now)
    db.commit()
    assert out["events"].count("unexpected_stop") == 1
    ev = db.scalar(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id, GeofenceEvent.event == "unexpected_stop"))
    assert ev.details["minutes"] >= 30
    # the fleet owner hears about it
    fleet_owner = db.scalar(select(User).where(User.email == "fleet@demo.agripulse"))
    from agripulse_api.models import Alert

    kinds = {a.kind for a in db.scalars(select(Alert).where(Alert.user_id == fleet_owner.id))}
    assert "unexpected_stop" in kinds
    # started 2 h ago on a short route and still far away -> delay alert
    assert "vehicle_delay" in kinds


def test_silent_phone_is_flagged(db, journey):
    t = _start(db, journey["trip"]["id"], 60)
    now = datetime.now(timezone.utc)
    process_points(db, t, [{"recorded_at": now - timedelta(minutes=45), "lat": 13.17, "lon": 78.07, "speed_kmph": 40}], now=now)
    db.commit()
    assert check_stale_trips(db, now=now)["flagged"] == 1
    assert check_stale_trips(db, now=now)["flagged"] == 0  # once only


def test_no_tracking_without_consent(db, journey):
    t = db.get(Trip, journey["trip"]["id"])
    t.status = "in_progress"
    db.commit()
    with pytest.raises(TrackingNotActive):
        process_points(db, t, [{"recorded_at": datetime.now(timezone.utc), "lat": 13.1, "lon": 78.0}])


def test_expired_share_link(client, as_role, db, journey):
    D = as_role("driver")
    tid = journey["trip"]["id"]
    client.post(f"/trips/{tid}/accept", headers=D)
    client.post(f"/trips/{tid}/consent", headers=D, json={"consent": True})
    client.post(f"/trips/{tid}/start", headers=D)
    t = db.get(Trip, tid)
    db.refresh(t)
    t.share_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert client.get(f"/public/track/{t.share_token}").status_code == 410


def test_in_transit_supply_and_live_websocket(client, as_role, db, journey):
    D = as_role("driver")
    tid, kolar = journey["trip"]["id"], journey["kolar"]
    client.post(f"/trips/{tid}/accept", headers=D)
    client.post(f"/trips/{tid}/consent", headers=D, json={"consent": True})
    client.post(f"/trips/{tid}/start", headers=D)
    token = as_role("farmer")["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/trips/{tid}?token={token}") as ws:
        assert ws.receive_json()["type"] == "position"  # initial snapshot
        client.post(f"/trips/{tid}/points", headers=D, json={"points": [
            {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": 13.17, "lon": 78.07, "speed_kmph": 35}]})
        msgs = [ws.receive_json() for _ in range(2)]
        assert any(m.get("type") == "position" and m.get("lat") == 13.17 for m in msgs)
    supply = client.get("/supply/in-transit", params={"mandi_id": kolar.id}, headers=as_role("policy")).json()[0]
    assert supply["tons_in_transit"] == 2 and supply["tons_real"] == 2 and supply["vehicles"] == []
    trader = client.get("/supply/in-transit", headers=as_role("trader")).json()[0]
    assert trader["vehicles"][0]["vehicle"] == "KA-01-XX-1234"


def test_simulator_marks_everything_simulated(db):
    import random

    from tracking import simulator

    ids = simulator.create_trips(db, 3, seed=1)
    st = simulator.SimState()
    st.trip_ids, st.speedup = ids, 60
    simulator.tick(db, st, 5, random.Random(1))
    for tid in ids:
        t = db.get(Trip, tid)
        assert t.is_simulated and t.vehicle.is_simulated and t.last_lat is not None


def test_public_websocket_by_share_token(client, as_role, db, journey):
    D = as_role("driver")
    tid = journey["trip"]["id"]
    client.post(f"/trips/{tid}/accept", headers=D)
    client.post(f"/trips/{tid}/consent", headers=D, json={"consent": True})
    client.post(f"/trips/{tid}/start", headers=D)
    db.expire_all()
    token = db.get(Trip, tid).share_token
    with client.websocket_connect(f"/ws/public/{token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "position" and "trip_id" not in first and "vehicle" not in first
        client.post(f"/trips/{tid}/points", headers=D, json={"points": [
            {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": 13.17, "lon": 78.07, "speed_kmph": 35}]})
        msg = ws.receive_json()
        assert msg["lat"] == 13.17 and set(msg) <= {"type", "status", "lat", "lon", "last_seen_at", "remaining_km",
                                                    "eta_at", "eta_local", "lots", "is_simulated"}
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/public/not-a-token") as ws:
            ws.receive_json()


def test_implausible_speed_is_dropped(db, journey):
    t = _start(db, journey["trip"]["id"], 5)
    now = datetime.now(timezone.utc)
    # 1 km in 1 s, then 1 km in 0.5 s with a reported speed: neither is a real truck speed
    process_points(db, t, [
        {"recorded_at": now - timedelta(seconds=3), "lat": 13.20, "lon": 78.02},
        {"recorded_at": now - timedelta(seconds=2), "lat": 13.209, "lon": 78.02},
        {"recorded_at": now - timedelta(seconds=1), "lat": 13.218, "lon": 78.02, "speed_kmph": 7200},
    ], now=now)
    db.commit()
    assert t.last_speed_kmph is None
