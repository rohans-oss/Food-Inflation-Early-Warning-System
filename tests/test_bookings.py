"""Farmer-direct transport booking -> fleet confirms by assigning a truck -> trip -> weighing -> recorded payment,
plus the public demo's SIMULATED autopilot."""
from datetime import datetime, timezone

from sqlalchemy import select

from agripulse_api.models import Lot, Mandi, TransportBooking, Trip, User, Vehicle
from tests.test_tracking import FARM, journey, seed_forecasts  # noqa: F401 (journey is a fixture)


def _chosen_lot(client, as_role, db, mandi="Kolar APMC"):
    seed_forecasts(db)
    lot = client.post("/lots", headers=as_role("farmer"), json={"quantity_tons": 2, "pickup_lat": FARM[0], "pickup_lon": FARM[1]}).json()
    m = db.scalar(select(Mandi).where(Mandi.name == mandi))
    client.post(f"/lots/{lot['id']}/preferred-mandi", headers=as_role("farmer"), json={"mandi_id": m.id})
    return lot, m


def _fleet(client, as_role, lot):
    ns = client.get(f"/lots/{lot['id']}/next-steps", headers=as_role("farmer")).json()
    return next(f for f in ns["fleets"] if f["name"] == "Hebbal Haulage (demo)")


def test_farmer_books_a_slot_and_the_fleet_declines_or_confirms(client, as_role, db):
    lot, kolar = _chosen_lot(client, as_role, db)
    F = as_role("farmer")
    fleet = _fleet(client, as_role, lot)
    slot = next(s for s in fleet["slots"] if s["free_trucks"] > 0)
    assert client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"],
                       "pickup_at": "2020-01-01T00:00:00Z"}).status_code == 400  # not an offered slot
    r = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "requested" and b["fare_estimate"] > 0
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "grouped"
    assert any(a["kind"] == "booking_requested" for a in client.get("/alerts", headers=as_role("fleet_owner")).json())
    # fleet declines -> lot is free again
    d = client.post(f"/bookings/{b['id']}/decline", headers=as_role("fleet_owner"), json={"reason": "Truck under repair"})
    assert d.status_code == 200 and d.json()["status"] == "declined"
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "registered"
    assert any(a["kind"] == "booking_declined" and "repair" in a["body"] for a in client.get("/alerts", headers=F).json())
    # rebook, and this time the fleet owner assigns a truck and driver: that confirms the booking
    b2 = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]}).json()
    v = db.scalar(select(Vehicle).where(Vehicle.registration == "KA-01-XX-1234"))
    drv = db.scalar(select(User).where(User.email == "driver@demo.agripulse"))
    t = client.post("/trips", headers=as_role("fleet_owner"), json={"shipment_id": b2["shipment_id"], "vehicle_id": v.id, "driver_id": drv.id})
    assert t.status_code == 201, t.text
    mine = client.get("/bookings", headers=F).json()
    assert mine[0]["status"] == "confirmed" and mine[0]["trip"]["vehicle"] == "KA-01-XX-1234"
    assert any(a["kind"] == "booking_confirmed" for a in client.get("/alerts", headers=F).json())
    assert client.post(f"/bookings/{b2['id']}/cancel", headers=F).status_code == 409  # truck already assigned


def test_trader_records_payment_and_farmer_confirms(client, as_role, db):
    lot, kolar = _chosen_lot(client, as_role, db)
    L = db.get(Lot, lot["id"])
    assert client.post(f"/lots/{lot['id']}/payment-received", headers=as_role("farmer")).status_code == 409
    # shortcut to a delivered lot at the demo trader's mandi (Kolar); the lifecycle itself is tested elsewhere
    from agripulse_api.routers.lots import make_shipment
    farmer = db.scalar(select(User).where(User.email == "farmer@demo.agripulse"))
    sh = make_shipment(db, farmer, kolar.id, [L.id])
    for s in ("in_transit", "at_mandi", "delivered"):
        L.status = s  # test setup only
    L.delivered_weight_kg, L.sale_price_per_quintal = 1970, 2100
    db.commit()
    assert client.post(f"/trader/lots/{lot['id']}/payment", headers=as_role("trader"), json={"method": "crypto"}).status_code == 422
    r = client.post(f"/trader/lots/{lot['id']}/payment", headers=as_role("trader"), json={"method": "upi", "upi_id": "farmer@upi", "reference": "UTR123"})
    assert r.status_code == 200 and r.json()["payout_status"] == "paid" and r.json()["payment"]["amount"] == 41370
    assert any(a["kind"] == "payment_recorded" and "41,370" in a["body"] for a in client.get("/alerts", headers=as_role("farmer")).json())
    assert client.post(f"/lots/{lot['id']}/payment-received", headers=as_role("farmer")).json()["payment"]["received_at"]


def test_demo_autopilot_runs_a_simulated_trip_end_to_end(client, as_role, db, monkeypatch):
    from agripulse_api.config import get_settings
    from agripulse_api.routers import bookings as bk

    lot, kolar = _chosen_lot(client, as_role, db)
    F = as_role("farmer")
    assert client.post(f"/lots/{lot['id']}/demo-trip", headers=F).status_code == 404  # off outside the public demo
    fleet = _fleet(client, as_role, lot)
    slot = next(s for s in fleet["slots"] if s["free_trucks"] > 0)
    b = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]}).json()
    trip_id, depot = bk._demo_prepare(db, lot["id"], b["id"])
    t = db.get(Trip, trip_id)
    assert t.is_simulated and t.vehicle.is_simulated and t.status == "in_progress"
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "grouped"  # truck still coming to the farm
    for i in range(1, 6):
        bk._demo_approach(db, trip_id, depot, i / 5)
    events = [e["event"] for e in client.get(f"/trips/{trip_id}", headers=F).json()["events"]]
    assert "reached_pickup" in events and "left_pickup_zone" not in events
    bk._demo_pickup(db, trip_id)
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "in_transit"
    for i in range(1, 11):
        bk._demo_step(db, trip_id, i / 10)
    events = [e["event"] for e in client.get(f"/trips/{trip_id}", headers=F).json()["events"]]
    assert "left_pickup_zone" in events and "reached_mandi" in events
    bk._demo_finish(db, lot["id"], trip_id)
    out = client.get(f"/lots/{lot['id']}", headers=F).json()
    assert out["status"] == "delivered" and out["payout_status"] == "paid" and out["payment"]["reference"].startswith("DEMO-")
    assert db.get(TransportBooking, b["id"]).status == "confirmed"
    # proof of delivery & sale: public, verifiable, and it says SIMULATED
    rc = client.get(f"/public/receipts/{out['receipt_token']}").json()
    assert rc["receipt_no"].startswith("AP-") and rc["simulated"] and rc["amount"] == out["payment"]["amount"]
    steps = {s["step"]: s for s in rc["timeline"]}
    assert all(steps[k]["verified"] for k in ("Truck reached the farm", "Picked up", "Reached the mandi",
                                              "Delivered at the gate", "Weighed and priced"))
    assert rc["gps_points"] >= 15 and rc["vehicle"].startswith(("SIM-", "KA-DEMO-"))
    assert client.get("/public/receipts/not-a-real-receipt-token").status_code == 404


def test_any_vegetable_can_be_registered_and_non_tomato_gets_mandis_without_a_price(client, as_role, db):
    seed_forecasts(db)
    crops = client.get("/crops").json()
    assert {"Tomato", "Onion", "Potato"} <= {c["name"] for c in crops}
    assert [c["name"] for c in crops if c["forecast"]] == ["Tomato"]
    F = as_role("farmer")
    # a vegetable that is not listed can be added (user request): it works, with no price
    custom = client.post("/lots", headers=F, json={"crop": "drumstick", "quantity_tons": 1, "pickup_lat": FARM[0],
                                                   "pickup_lon": FARM[1]})
    assert custom.status_code == 201 and custom.json()["crop"] == "Drumstick"
    assert "Drumstick" in {c["name"] for c in client.get("/crops").json() if c["custom"]}
    assert client.post("/lots", headers=F, json={"crop": "<b>", "quantity_tons": 1, "pickup_lat": FARM[0],
                                                 "pickup_lon": FARM[1]}).status_code == 400
    lot = client.post("/lots", headers=F, json={"crop": "onion", "quantity_tons": 3, "pickup_lat": FARM[0],
                                                "pickup_lon": FARM[1]}).json()
    assert lot["crop"] == "Onion" and lot["crop_has_forecast"] is False
    rec = client.get("/recommend/best-mandi", headers=F, params={"lot_id": lot["id"]}).json()
    assert rec["no_price_forecast"] and rec["ranked"] and rec["ranked"][0]["price_forecast"] is None
    assert rec["ranked"][0]["transport_cost"] <= rec["ranked"][-1]["transport_cost"]
    tomato = client.post("/lots", headers=F, json={"quantity_tons": 3, "pickup_lat": FARM[0], "pickup_lon": FARM[1]}).json()
    t_rec = client.get("/recommend/best-mandi", headers=F, params={"lot_id": tomato["id"]}).json()
    assert t_rec["ranked"][0]["price_forecast"]["p50"] > 0
    # onion spoils slower than tomato on the same road (config/recommender.toml crop sensitivity)
    same = next(r for r in rec["ranked"] if r["mandi_id"] == t_rec["ranked"][0]["mandi_id"])
    assert same["spoilage_pct"] < t_rec["ranked"][0]["spoilage_pct"]


def test_weighing_issues_a_receipt(client, as_role, db, journey):
    """The real (non-demo) path: trader weighs -> receipt with the QR scans as evidence."""
    from datetime import datetime, timedelta, timezone

    from tests.test_tracking import path

    D, lot, trip, kolar = as_role("driver"), journey["lot"], journey["trip"], journey["kolar"]
    client.post(f"/trips/{trip['id']}/accept", headers=D)
    client.post(f"/trips/{trip['id']}/consent", headers=D, json={"consent": True})
    client.post(f"/trips/{trip['id']}/start", headers=D)
    tok = client.get(f"/lots/{lot['id']}", headers=as_role("farmer")).json()["trip"]["pickup_qr_token"]
    client.post(f"/trips/{trip['id']}/scan/pickup", headers=D, json={"token": tok})
    t0 = datetime.now(timezone.utc) - timedelta(seconds=45)
    pts = [{"recorded_at": (t0 + timedelta(seconds=i)).isoformat(), "lat": a, "lon": b, "speed_kmph": 40}
           for i, (a, b) in enumerate(path(FARM, (kolar.lat, kolar.lon), 40))]
    client.post(f"/trips/{trip['id']}/points", headers=D, json={"points": pts})
    dtok = client.get(f"/trips/{trip['id']}", headers=D).json()["delivery_qr_token"]
    client.post(f"/trips/{trip['id']}/scan/delivery", headers=as_role("trader"), json={"token": dtok})
    client.post(f"/trader/lots/{lot['id']}/weigh", headers=as_role("trader"), json={"weight_kg": 1985, "price_per_quintal": 1400})
    out = client.get(f"/lots/{lot['id']}", headers=as_role("farmer")).json()
    rc = client.get(f"/public/receipts/{out['receipt_token']}").json()
    assert rc["amount"] == 27790 and rc["buyer"] and not rc["simulated"] and rc["payment"]["status"] == "pending"
    steps = {s["step"]: s["verified"] for s in rc["timeline"]}
    assert steps["Picked up"] and steps["Reached the mandi"] and steps["Delivered at the gate"] and steps["Weighed and priced"]


def test_several_transporters_near_the_farm_cheapest_first_and_the_demo_truck_comes_from_its_base(client, as_role, db):
    from agripulse_api.models import Organization

    lot, _ = _chosen_lot(client, as_role, db)
    F = as_role("farmer")
    fleets = client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()["fleets"]
    names = [f["name"] for f in fleets]
    assert len(fleets) >= 4 and "Kolar Krishi Transport (demo)" in names
    assert "Mysuru Agri Logistics (demo)" not in names  # base too far from a Kolar-belt farm
    fares = [f["fare_estimate"] for f in fleets if f["fit"]]
    assert fares == sorted(fares)
    kolar = next(f for f in fleets if f["name"] == "Kolar Krishi Transport (demo)")
    assert kolar["base"] == "Kolar" and kolar["drivers"] and kolar["base_km_from_farm"] < 40
    slot = next(s for s in kolar["slots"] if s["free_trucks"] > 0)
    b = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": kolar["org_id"], "pickup_at": slot["pickup_at"]}).json()
    from agripulse_api.routers import bookings as bk

    trip_id, depot = bk._demo_prepare(db, lot["id"], b["id"])
    t = db.get(Trip, trip_id)
    org = db.scalar(select(Organization).where(Organization.name == "Kolar Krishi Transport (demo)"))
    assert t.fleet_org_id == org.id and t.vehicle.registration.startswith("KA-DEMO-") and t.vehicle.capacity_tons >= 2
    assert db.get(User, t.driver_id).org_id == org.id
    assert depot == (org.base_lat, org.base_lon)  # the truck comes from the transporter's own base


def test_driver_tells_the_farmer_a_code_and_the_farmer_confirms_the_handover(client, as_role, db):
    from agripulse_api.routers import bookings as bk

    lot, _ = _chosen_lot(client, as_role, db)
    F = as_role("farmer")
    fleet = _fleet(client, as_role, lot)
    slot = next(s for s in fleet["slots"] if s["free_trucks"] > 0)
    b = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]}).json()
    assert client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": "0000"}).status_code == 409  # no truck yet
    trip_id, _ = bk._demo_prepare(db, lot["id"], b["id"])  # transporter confirms, driver starts
    t = db.get(Trip, trip_id)
    assert t.pickup_code and len(t.pickup_code) == 4
    p = client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()["pickup"]
    assert p["driver"] and p["vehicle"] == t.vehicle.registration and p["started"] and not p["picked_up"]
    assert "demo_code" not in p  # outside the public demo the farmer only learns it from the driver
    assert "pickup_code" not in client.get(f"/trips/{trip_id}", headers=F).json()
    driver = db.get(User, t.driver_id)
    from tests.conftest import login

    assert client.get(f"/trips/{trip_id}", headers=login(client, driver.email)).json()["pickup_code"] == t.pickup_code
    wrong = "1111" if t.pickup_code != "1111" else "2222"
    r = client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": wrong})
    assert r.status_code == 400 and "4 tries left" in r.json()["detail"]
    ok = client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": t.pickup_code}).json()
    assert ok["status"] == "in_transit"
    ev = [e["event"] for e in client.get(f"/trips/{trip_id}", headers=F).json()["events"]]
    assert "picked_up" in ev


def test_wrong_codes_lock_after_five_tries(client, as_role, db):
    from agripulse_api.routers import bookings as bk

    lot, _ = _chosen_lot(client, as_role, db)
    F = as_role("farmer")
    fleet = _fleet(client, as_role, lot)
    slot = next(s for s in fleet["slots"] if s["free_trucks"] > 0)
    b = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]}).json()
    trip_id, _ = bk._demo_prepare(db, lot["id"], b["id"])
    code = db.get(Trip, trip_id).pickup_code
    wrong = "1111" if code != "1111" else "2222"
    for _ in range(5):
        assert client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": wrong}).status_code == 400
    assert client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": code}).status_code == 429


def test_demo_traffic_gives_the_mandi_trucks_and_deliveries_to_pay(client, as_role, db):
    from agripulse_api.routers import bookings as bk

    plan = bk.make_demo_traffic(db)
    assert [m for _, m in plan] == ["drive", "drive", "delivered", "delivered"]
    assert bk.make_demo_traffic(db) == []  # not again within a few hours
    for sid, mode in plan:
        if mode == "delivered":
            bk._deliver_now(db, sid)
    tid, depot = bk._demo_prepare_shipment(db, plan[0][0])
    bk._demo_approach(db, tid, depot, 1.0)
    bk._demo_pickup(db, tid)  # background trucks: the driver scans the QR, nobody types a code
    board = client.get("/trader/board", headers=as_role("trader")).json()
    assert {x["farmer"] for x in board["delivered_last_24h"]} >= {"Krishnappa", "Yellamma"}
    assert all(x["payout_status"] == "pending" for x in board["delivered_last_24h"] if x["farmer"] in {"Krishnappa", "Yellamma"})
    assert any(i["trip_id"] == tid for i in board["incoming"])


def test_persistent_demo_url_keeps_the_query_and_sets_the_schema():
    import sys

    sys.path.insert(0, "scripts")
    from demo_start import with_search_path

    u = with_search_path("postgresql://u:p@h/db?sslmode=require&channel_binding=require", "demo")
    assert "sslmode=require" in u and "channel_binding=require" in u and "options=-csearch_path%3Ddemo%2Cpublic" in u


def test_bank_payment_needs_full_details_and_keeps_only_the_last_four_digits(client, as_role, db):
    from agripulse_api.routers import bookings as bk

    plan = bk.make_demo_traffic(db)
    sid = next(s for s, m in plan if m == "delivered")
    bk._deliver_now(db, sid)
    lot = db.scalar(select(Lot).where(Lot.shipment_id == sid))
    T = as_role("trader")
    url = f"/trader/lots/{lot.id}/payment"
    r = client.post(url, headers=T, json={"method": "bank", "account_number": "123456789012"})
    assert r.status_code == 400 and "account holder name" in r.json()["detail"]
    bad = {"method": "bank", "account_holder": "Krishnappa", "account_number": "1234 5678 9012", "ifsc": "sbin001234",
           "bank_name": "State Bank of India", "branch": "Chintamani"}
    assert "IFSC" in client.post(url, headers=T, json=bad).json()["detail"]
    ok = client.post(url, headers=T, json={**bad, "ifsc": "SBIN0001234", "reference": "UTR123456"}).json()
    d = ok["payment"]["details"]
    assert ok["payout_status"] == "paid" and d["account_last4"] == "9012" and d["ifsc"] == "SBIN0001234"
    assert "123456789012" not in str(ok) and "account_number" not in d  # the full number is never stored


def test_upi_payment_needs_a_upi_id(client, as_role, db):
    from agripulse_api.routers import bookings as bk

    plan = bk.make_demo_traffic(db)
    sid = [s for s, m in plan if m == "delivered"][1]
    bk._deliver_now(db, sid)
    lot = db.scalar(select(Lot).where(Lot.shipment_id == sid))
    url = f"/trader/lots/{lot.id}/payment"
    assert client.post(url, headers=as_role("trader"), json={"method": "upi", "upi_id": "nope"}).status_code == 400
    ok = client.post(url, headers=as_role("trader"), json={"method": "upi", "upi_id": "yellamma@okaxis"}).json()
    assert ok["payment"]["details"] == {"upi_id": "yellamma@okaxis"}


def test_a_real_mandi_manager_weighs_and_pays_never_the_demo(client, as_role, db):
    """Real farmer -> demo transporter -> a mandi with a REAL manager: the demo truck stops at the gate; the real
    manager sees the lot, weighs it and records the payment."""
    from agripulse_api.routers import bookings as bk

    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    reg = client.post("/auth/register", json={"email": "ravi@farm.in", "password": "longenough", "full_name": "Ravi Kumar",
                                              "role": "farmer"}).json()
    F = {"Authorization": f"Bearer {reg['access_token']}"}
    mgr = client.post("/auth/register", json={"email": "manager@kolarapmc.in", "password": "longenough", "full_name": "Kolar manager",
                                              "role": "trader", "district": kolar.district, "mandi_id": kolar.id}).json()
    M = {"Authorization": f"Bearer {mgr['access_token']}"}
    lot = client.post("/lots", headers=F, json={"crop": "Tomato", "quantity_tons": 2, "pickup_lat": FARM[0], "pickup_lon": FARM[1]}).json()
    client.post(f"/lots/{lot['id']}/preferred-mandi", headers=F, json={"mandi_id": kolar.id})
    fleet = next(f for f in client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()["fleets"] if f["name"].endswith("(demo)"))
    slot = next(s for s in fleet["slots"] if s["free_trucks"] > 0)
    b = client.post(f"/lots/{lot['id']}/bookings", headers=F, json={"fleet_org_id": fleet["org_id"], "pickup_at": slot["pickup_at"]}).json()
    trip_id, depot = bk._demo_prepare(db, lot["id"], b["id"])
    bk._demo_approach(db, trip_id, depot, 1.0)
    code = db.get(Trip, trip_id).pickup_code
    assert client.post(f"/lots/{lot['id']}/confirm-pickup", headers=F, json={"code": code}).json()["status"] == "in_transit"
    assert any(i["trip_id"] == trip_id for i in client.get("/trader/board", headers=M).json()["incoming"])
    bk._demo_step(db, trip_id, 1.0)
    bk._demo_finish(db, None, trip_id)
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "at_mandi"  # left for the real manager
    waiting = client.get("/trader/board", headers=M).json()["awaiting_weighing"]
    assert lot["id"] in [x["lot_id"] for x in waiting]
    w = client.post(f"/trader/lots/{lot['id']}/weigh", headers=M, json={"weight_kg": 1985, "price_per_quintal": 2200})
    assert w.status_code == 200
    p = client.post(f"/trader/lots/{lot['id']}/payment", headers=M, json={"method": "upi", "upi_id": "ravi@okicici", "reference": "T1"})
    out = client.get(f"/lots/{lot['id']}", headers=F).json()
    assert p.status_code == 200 and out["payout_status"] == "paid" and out["payment"]["details"]["upi_id"] == "ravi@okicici"


def test_the_demo_never_confirms_for_a_real_transport_company(client, db):
    from agripulse_api.models import Organization, Shipment
    from agripulse_api.routers import bookings as bk

    r = client.post("/auth/register", json={"email": "owner@realfleet.in", "password": "longenough", "full_name": "Owner",
                                            "role": "fleet_owner", "org_name": "Real Roadways", "district": "Kolar"})
    org = db.scalar(select(Organization).where(Organization.name == "Real Roadways"))
    assert r.status_code == 201 and org.base_label  # base from the district
    sh = Shipment(org_id=None, mandi_id=1, created_by=1, fleet_org_id=org.id, status="booked")
    db.add(sh)
    db.commit()
    assert bk.start_demo_shipment(sh.id) is False
