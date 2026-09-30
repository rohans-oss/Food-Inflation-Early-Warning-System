"""Farmer-direct transport booking -> fleet confirms by assigning a truck -> trip -> weighing -> recorded payment,
plus the public demo's SIMULATED autopilot."""
from datetime import datetime, timezone

from sqlalchemy import select

from agripulse_api.models import Lot, Mandi, TransportBooking, Trip, User, Vehicle
from tests.test_tracking import FARM, seed_forecasts


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
    r = client.post(f"/trader/lots/{lot['id']}/payment", headers=as_role("trader"), json={"method": "upi", "reference": "UTR123"})
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
    trip_id = bk._demo_prepare(db, lot["id"], b["id"])
    t = db.get(Trip, trip_id)
    assert t.is_simulated and t.vehicle.is_simulated and t.status == "in_progress"
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["status"] == "in_transit"
    for i in range(1, 11):
        bk._demo_step(db, trip_id, i / 10)
    bk._demo_finish(db, lot["id"], trip_id)
    out = client.get(f"/lots/{lot['id']}", headers=F).json()
    assert out["status"] == "delivered" and out["payout_status"] == "paid" and "simulated" in out["payment"]["method"]
    assert db.get(TransportBooking, b["id"]).status == "confirmed"
