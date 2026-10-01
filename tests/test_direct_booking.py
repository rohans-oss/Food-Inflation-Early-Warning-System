"""Direct farmer -> driver booking with real accounts (the two-phone field test, end to end).

A real fleet owner adds a driver by phone; the driver signs up with their own truck, goes online for Kolar / Kolar APMC;
a real farmer in Kolar requests a driver; the driver's WebSocket gets the request within the same request cycle and a
Web Push is handed to the push service; first accept wins; the trip is real (is_simulated = false, channel "direct")
and continues through the existing pickup-code / consent / GPS flow.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agripulse_api import webpush
from agripulse_api.models import AuditLog, DriverAvailability, Mandi, Trip, TripRequest, TripRequestOffer
from agripulse_api.routers import direct

FARM = (13.18, 78.15)  # Munrandahalli, Kolar district


@pytest.fixture()
def pushes(monkeypatch):
    sent = []

    def fake_sender(sub, data, private_key, claims):
        sent.append({"endpoint": sub["endpoint"], "data": data, "claims": claims})
        return 201

    monkeypatch.setattr(webpush, "SENDER", fake_sender)
    webpush.reset_cache()
    return sent


def _auth(r):
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def world(client, db):
    """Real fleet owner, two invited drivers with their own trucks, one real farmer with a lot. No demo accounts."""
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    owner = _auth(client.post("/auth/register", json={"email": "owner@kolarlorry.in", "password": "longenough",
                                                      "full_name": "Owner", "role": "fleet_owner",
                                                      "org_name": "Kolar Lorry Service", "district": "Kolar"}))
    drivers = []
    for i, (name, phone, reg) in enumerate((("Ravi Kumar", "9000000001", "KA07AB1234"),
                                             ("Shankar", "9000000002", "KA07CD5678"))):
        assert client.post("/drivers", headers=owner, json={"name": name, "phone": phone}).status_code == 201
        drivers.append(_auth(client.post("/auth/register", json={
            "email": f"d{i}@kolarlorry.in", "password": "longenough", "full_name": name, "role": "driver",
            "phone": phone, "district": "Kolar", "vehicle_registration": reg, "vehicle_capacity_tons": 5})))
    farmer = _auth(client.post("/auth/register", json={"email": "suma@farm.in", "password": "longenough",
                                                       "full_name": "Suma", "role": "farmer", "phone": "9876500011"}))
    lot = client.post("/lots", headers=farmer, json={"crop": "Tomato", "quantity_tons": 2, "pickup_label": "Munrandahalli",
                                                     "pickup_lat": FARM[0], "pickup_lon": FARM[1]}).json()
    return {"kolar": kolar, "owner": owner, "d1": drivers[0], "d2": drivers[1], "farmer": farmer, "lot": lot}


def go_online(client, h, mandi_id, district="Kolar"):
    a = client.get("/driver/availability", headers=h).json()
    assert a["vehicles"], "the driver's own truck must be listed"
    r = client.put("/driver/availability", headers=h, json={"online": True, "district": district, "mandi_ids": [mandi_id],
                                                            "vehicle_id": a["vehicles"][0]["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def subscribe_push(client, h, n):
    r = client.post("/push/subscribe", headers=h, json={"endpoint": f"https://fcm.googleapis.com/fcm/send/phone-{n}",
                                                        "keys": {"p256dh": "BPk" + "a" * 60, "auth": "x" * 16}})
    assert r.status_code == 201


def ws_for(client, h):
    t = client.post("/auth/ws-ticket", headers=h).json()["ticket"]
    ws = client.websocket_connect(f"/ws/live?ticket={t}")
    s = ws.__enter__()
    assert s.receive_json()["type"] == "hello"
    return ws, s


def test_zero_drivers_gives_a_clear_message_not_a_hang(client, world, db):
    w = world
    r = client.post(f"/lots/{w['lot']['id']}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id})
    assert r.status_code == 201
    out = r.json()
    assert out["status"] == "no_drivers" and out["drivers_notified"] == 0
    assert out["reason"] == "No drivers are currently available for Kolar APMC (Kolar)."
    # a driver who is online but for ANOTHER mandi, or another district, is not matched
    other = db.scalar(select(Mandi).where(Mandi.district == "Kolar", Mandi.id != w["kolar"].id))
    go_online(client, w["d1"], other.id)
    assert client.get(f"/lots/{w['lot']['id']}/drivers-available", headers=w["farmer"],
                      params={"mandi_id": w["kolar"].id}).json()["drivers"] == 0
    # online for Kolar APMC but last seen too long ago (phone died, no push): not matched either
    go_online(client, w["d1"], w["kolar"].id)
    a = db.get(DriverAvailability, client.get("/auth/me", headers=w["d1"]).json()["id"])
    a.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=direct.FRESH_S + 5)
    db.commit()
    assert client.get(f"/lots/{w['lot']['id']}/drivers-available", headers=w["farmer"],
                      params={"mandi_id": w["kolar"].id}).json()["drivers"] == 0
    # ...but with Web Push on that phone it can still be woken, so it is matched
    subscribe_push(client, w["d1"], 1)
    a.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=direct.FRESH_S + 5)
    db.commit()
    assert client.get(f"/lots/{w['lot']['id']}/drivers-available", headers=w["farmer"],
                      params={"mandi_id": w["kolar"].id}).json()["drivers"] == 1


def test_request_reaches_the_driver_live_and_first_accept_wins(client, world, db, pushes):
    w = world
    go_online(client, w["d1"], w["kolar"].id)
    go_online(client, w["d2"], w["kolar"].id)
    subscribe_push(client, w["d1"], 1)
    ws, sock = ws_for(client, w["d1"])
    fws, fsock = ws_for(client, w["farmer"])
    try:
        assert client.get(f"/lots/{w['lot']['id']}/drivers-available", headers=w["farmer"],
                          params={"mandi_id": w["kolar"].id}).json()["drivers"] == 2
        r = client.post(f"/lots/{w['lot']['id']}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id})
        req = r.json()
        assert r.status_code == 201 and req["status"] == "notified" and req["drivers_notified"] == 2
        assert 0 < req["seconds_left"] <= direct.REQUEST_TTL_S and req["estimated_km"] > 0
        # real time: the driver's socket gets the request (and the in-app alert), no refresh needed
        msgs = [sock.receive_json() for _ in range(2)]
        live = next(m for m in msgs if m["type"] == "trip_request")
        assert live["request_id"] == req["id"] and live["farmer"] == "Suma" and live["crop"] == "Tomato"
        assert live["tons"] == 2 and live["mandi"] == "Kolar APMC" and live["village"] == "Munrandahalli"
        assert live["road_km"] > 0 and live["driver_pay_estimate"] > 0
        assert any(m["type"] == "alert" for m in msgs)
        # ...and the locked-phone path: one Web Push handed to the push service for driver 1's phone
        import time

        for _ in range(50):
            if pushes:
                break
            time.sleep(0.05)
        assert len(pushes) == 1 and '"request_id": %d' % req["id"] in pushes[0]["data"]
        # the driver's list shows it (and marks it seen); the phone number stays private until accepted
        offers = client.get("/driver/requests", headers=w["d1"]).json()
        assert [o["request_id"] for o in offers] == [req["id"]] and "farmer_phone" not in offers[0]
        # driver 1 accepts -> trip at "accepted", real, direct; driver 2 is too late
        t = client.post(f"/driver/requests/{req['id']}/accept", headers=w["d1"])
        assert t.status_code == 200, t.text
        trip = t.json()
        assert trip["status"] == "accepted" and trip["booking_channel"] == "direct" and trip["is_simulated"] is False
        assert trip["pickups"][0]["farmer"] == "Suma" and trip["pickups"][0]["phone"] == "9876500011"
        late = client.post(f"/driver/requests/{req['id']}/accept", headers=w["d2"])
        assert late.status_code == 409 and "another driver" in late.json()["detail"]
        assert client.get("/driver/requests", headers=w["d2"]).json() == []
        # the farmer's socket hears it at once; both sides read the same trip id and status from the server
        upd = [fsock.receive_json() for _ in range(2)]
        u = next(m for m in upd if m["type"] == "trip_request_update")
        assert u["status"] == "accepted" and u["trip_id"] == trip["id"]
        fr = client.get(f"/lots/{w['lot']['id']}/driver-request", headers=w["farmer"]).json()
        assert fr["trip"]["id"] == trip["id"] and fr["trip"]["status"] == "accepted" and fr["trip"]["driver"] == "Ravi Kumar"
        ns = client.get(f"/lots/{w['lot']['id']}/next-steps", headers=w["farmer"]).json()
        assert ns["pickup"]["trip_id"] == trip["id"] and ns["pickup"]["vehicle"] == "KA07AB1234"
        assert "demo_code" not in ns["pickup"] and ns["driver_request"]["status"] == "accepted" and not ns["via_fpo"]
    finally:
        ws.__exit__(None, None, None)
        fws.__exit__(None, None, None)
    db.expire_all()
    row = db.get(Trip, trip["id"])
    assert row.is_simulated is False and row.booking_channel == "direct" and row.shipment.is_simulated is False
    offers = {o.driver_id: o.status for o in db.scalars(select(TripRequestOffer))}
    assert sorted(offers.values()) == ["accepted", "withdrawn"]
    states = [a.to_state for a in db.scalars(select(AuditLog).where(AuditLog.entity == "trip_request")
                                             .order_by(AuditLog.id))]
    assert states == ["requested", "notified", "accepted"]

    # from here the EXISTING flow: consent, start, the farmer enters the driver's pickup code, GPS...
    d1 = w["d1"]
    code = client.get(f"/trips/{trip['id']}", headers=d1).json()["pickup_code"]
    assert client.post(f"/trips/{trip['id']}/consent", headers=d1, json={"consent": True}).status_code == 200
    assert client.post(f"/trips/{trip['id']}/start", headers=d1).status_code == 200
    from agripulse_api.routers import bookings as bk

    assert bk.start_demo_shipment(row.shipment_id) is False  # never the autopilot
    ok = client.post(f"/lots/{w['lot']['id']}/confirm-pickup", headers=w["farmer"], json={"code": code})
    assert ok.status_code == 200 and ok.json()["status"] == "in_transit"
    gps = client.post(f"/trips/{trip['id']}/points", headers=d1, json={"points": [
        {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": FARM[0] + 0.01, "lon": FARM[1] - 0.01,
         "speed_kmph": 35, "accuracy_m": 8}]})
    assert gps.status_code in (200, 202), gps.text
    # Admin tells this trip apart from FPO / company / demo trips
    ch = client.get("/admin/booking-channels", headers=_admin(client)).json()
    direct_row = next(c for c in ch["channels"] if c["channel"] == "direct")
    assert direct_row["real"] == 1 and direct_row["simulated"] == 0
    assert ch["requests"][0]["status"] == "accepted" and ch["requests"][0]["first_seen_s"] is not None


def _admin(client):
    from tests.conftest import login

    return login(client, "admin@agripulse.local", "agripulse-admin")


def test_decline_by_everyone_then_expiry_and_cancel(client, world, db):
    w = world
    go_online(client, w["d1"], w["kolar"].id)
    lot = w["lot"]["id"]
    req = client.post(f"/lots/{lot}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id}).json()
    assert req["drivers_notified"] == 1
    again = client.post(f"/lots/{lot}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id})
    assert again.status_code == 409  # one open request per lot
    d = client.post(f"/driver/requests/{req['id']}/decline", headers=w["d1"]).json()
    assert d["offer_status"] == "declined" and d["request_status"] == "declined"
    out = client.get(f"/lots/{lot}/driver-request", headers=w["farmer"]).json()
    assert out["status"] == "declined" and "said no" in out["reason"]
    assert client.post(f"/driver/requests/{req['id']}/accept", headers=w["d1"]).status_code == 409

    # nobody answers -> expired after the TTL; the farmer sees why and can ask again
    req2 = client.post(f"/lots/{lot}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id}).json()
    r = db.get(TripRequest, req2["id"])
    r.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    out = client.get(f"/lots/{lot}/driver-request", headers=w["farmer"]).json()
    assert out["status"] == "expired" and client.get("/driver/requests", headers=w["d1"]).json() == []
    assert client.post(f"/driver/requests/{req2['id']}/accept", headers=w["d1"]).status_code == 409

    # the farmer cancels a waiting request
    req3 = client.post(f"/lots/{lot}/driver-request", headers=w["farmer"], json={"mandi_id": w["kolar"].id}).json()
    c = client.post(f"/driver-requests/{req3['id']}/cancel", headers=w["farmer"])
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert client.get("/driver/requests", headers=w["d1"]).json() == []
    # the lot is still free for the other paths (company booking / FPO)
    assert client.get(f"/lots/{lot}", headers=w["farmer"]).json()["status"] == "registered"


def test_only_real_accounts_and_real_trucks(client, world, as_role, db):
    w = world
    # demo accounts can't take part (rule 30: real accounts only)
    assert client.put("/driver/availability", headers=as_role("driver"), json={
        "online": True, "district": "Kolar", "mandi_ids": [w["kolar"].id], "vehicle_id": None}).status_code == 403
    demo_lot = client.post("/lots", headers=as_role("farmer"), json={"quantity_tons": 1, "pickup_lat": FARM[0],
                                                                     "pickup_lon": FARM[1]}).json()
    assert client.post(f"/lots/{demo_lot['id']}/driver-request", headers=as_role("farmer"),
                       json={"mandi_id": w["kolar"].id}).status_code == 403
    # a sample truck is refused; going online needs district, mandi and a truck
    from agripulse_api.models import Vehicle

    me = client.get("/auth/me", headers=w["d1"]).json()
    sample = Vehicle(org_id=me["org_id"], registration="KA-DEMO-XYZ", capacity_tons=5, is_simulated=True)
    db.add(sample)
    db.commit()
    bad = client.put("/driver/availability", headers=w["d1"], json={"online": True, "district": "Kolar",
                                                                    "mandi_ids": [w["kolar"].id], "vehicle_id": sample.id})
    assert bad.status_code == 400 and "sample truck" in bad.json()["detail"]
    assert all(v["registration"] != "KA-DEMO-XYZ" for v in client.get("/driver/availability", headers=w["d1"]).json()["vehicles"])
    assert client.put("/driver/availability", headers=w["d1"], json={"online": True, "district": "Kolar",
                                                                     "mandi_ids": []}).status_code == 400
    # a load bigger than the truck is not offered to that driver
    go_online(client, w["d1"], w["kolar"].id)
    big = client.post("/lots", headers=w["farmer"], json={"crop": "Onion", "quantity_tons": 9, "pickup_lat": FARM[0],
                                                          "pickup_lon": FARM[1]}).json()
    assert client.post(f"/lots/{big['id']}/driver-request", headers=w["farmer"],
                       json={"mandi_id": w["kolar"].id}).json()["status"] == "no_drivers"
    # offline -> not matched
    client.put("/driver/availability", headers=w["d1"], json={"online": False})
    assert client.get(f"/lots/{w['lot']['id']}/drivers-available", headers=w["farmer"],
                      params={"mandi_id": w["kolar"].id}).json()["drivers"] == 0


def test_existing_company_booking_and_fpo_flows_are_labelled_by_channel(client, as_role, db):
    """The other two paths still work and are told apart from direct trips in Admin."""
    from agripulse_api.routers import bookings as bk

    bk.seed_driver_history(db)  # demo autopilot trips
    ch = {c["channel"]: c for c in client.get("/admin/booking-channels", headers=_admin(client)).json()["channels"]}
    assert ch["demo"]["simulated"] > 0 and "direct" not in ch


def test_vapid_keys_are_generated_once_and_kept(db):
    webpush.reset_cache()
    k1 = webpush.vapid_keys(db)
    webpush.reset_cache()
    assert webpush.vapid_keys(db) == k1 and len(k1[1]) > 80  # uncompressed P-256 point, base64url


def test_drive_in_the_app_signs_the_phone_in_without_a_password(client, world, as_role):
    """Web "Drive in the app": a 2-minute single-use ticket -> the phone app gets its OWN session."""
    w = world
    t = client.post("/auth/handoff-ticket", headers=w["d1"])
    assert t.status_code == 200 and t.json()["expires_in"] == 120
    r = client.post("/auth/handoff", json={"ticket": t.json()["ticket"]})
    assert r.status_code == 200 and r.json()["user"]["full_name"] == "Ravi Kumar"
    phone = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/driver/availability", headers=phone).status_code == 200
    again = client.post("/auth/handoff", json={"ticket": t.json()["ticket"]})
    assert again.status_code == 401 and "expired" in again.json()["detail"]  # single use
    access = w["d1"]["Authorization"].split()[1]
    assert client.post("/auth/handoff", json={"ticket": access}).status_code == 401  # an access token is not a ticket
    assert client.post("/auth/handoff-ticket", headers=w["farmer"]).status_code == 403  # drivers only
    # signing out on the web kills unused tickets of that session
    t2 = client.post("/auth/handoff-ticket", headers=w["d1"]).json()["ticket"]
    client.post("/auth/logout", headers=w["d1"])
    assert client.post("/auth/handoff", json={"ticket": t2}).status_code == 401
