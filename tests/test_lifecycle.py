from datetime import date, timedelta

import pytest
from sqlalchemy import select

from agripulse_api.lifecycle import InvalidTransition, can_move, move
from agripulse_api.models import AuditLog, Lot, Mandi, Price, User
from agripulse_api.supply import cost_config, spoilage_pct
from tests.test_tracking import FARM, journey  # noqa: F401  (fixture)


def test_refresh_token_flow(client):
    r = client.post("/auth/login", json={"email": "farmer@demo.agripulse", "password": "agripulse-demo"}).json()
    assert r["refresh_token"] and r["refresh_token"] != r["access_token"]
    # a refresh token is not an access token
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {r['refresh_token']}"}).status_code == 401
    new = client.post("/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert new.status_code == 200
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {new.json()['access_token']}"}).status_code == 200
    # an access token can't be used to refresh
    assert client.post("/auth/refresh", json={"refresh_token": r["access_token"]}).status_code == 401


def test_refresh_denied_after_disable(client, as_role):
    r = client.post("/auth/login", json={"email": "buyer@demo.agripulse", "password": "agripulse-demo"}).json()
    client.patch(f"/admin/users/{r['user']['id']}", json={"is_active": False}, headers=as_role("admin"))
    assert client.post("/auth/refresh", json={"refresh_token": r["refresh_token"]}).status_code == 401


def test_state_machine_rejects_skips(db):
    farmer = db.scalar(select(User).where(User.role == "farmer"))
    lot = Lot(farmer_id=farmer.id, quantity_tons=1, pickup_lat=13.1, pickup_lon=78.1)
    db.add(lot)
    db.flush()
    assert not can_move(lot, "delivered")
    with pytest.raises(InvalidTransition):
        move(db, lot, "delivered", farmer.id)  # registered -> delivered skips the chain
    with pytest.raises(Exception):
        move(db, lot, "paid", farmer.id, field="payout_status")  # not delivered yet
    for to in ("grouped", "in_transit", "at_mandi", "delivered"):
        move(db, lot, to, farmer.id)
    move(db, lot, "paid", farmer.id, field="payout_status")
    with pytest.raises(InvalidTransition):
        move(db, lot, "in_transit", farmer.id)  # delivered is terminal
    db.flush()
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "lot", AuditLog.entity_id == lot.id)).all()
    assert [r.to_state for r in rows] == ["grouped", "in_transit", "at_mandi", "delivered", "paid"]


def test_journey_writes_a_complete_audit_trail(client, as_role, db, journey):  # noqa: F811
    D = as_role("driver")
    tid, lot_id = journey["trip"]["id"], journey["lot"]["id"]
    client.post(f"/trips/{tid}/accept", headers=D)
    assert client.post(f"/trips/{tid}/accept", headers=D).status_code == 409  # accepting twice
    client.post(f"/trips/{tid}/consent", headers=D, json={"consent": True})
    client.post(f"/trips/{tid}/start", headers=D)
    token = client.get(f"/lots/{lot_id}", headers=as_role("farmer")).json()["trip"]["pickup_qr_token"]
    client.post(f"/trips/{tid}/scan/pickup", headers=D, json={"token": token})
    # weighing before the delivery scan is refused
    assert client.post(f"/trader/lots/{lot_id}/weigh", headers=as_role("trader"),
                       json={"weight_kg": 2000, "price_per_quintal": 1500}).status_code in (404, 409)
    hist = client.get(f"/lots/{lot_id}/history", headers=as_role("farmer")).json()
    trail = [(h["entity"], h["to"]) for h in hist]
    for step in [("lot", "registered"), ("shipment", "planned"), ("lot", "grouped"), ("shipment", "booked"),
                 ("trip", "assigned"), ("trip", "accepted"), ("trip", "in_progress"), ("shipment", "in_transit"),
                 ("lot", "in_transit")]:
        assert step in trail, step
    assert all(h["actor_id"] for h in hist)
    # other farmers can't read it
    other = client.post("/auth/register", json={"email": "o2@x.in", "password": "longenough", "full_name": "O", "role": "farmer"})
    assert client.get(f"/lots/{lot_id}/history",
                      headers={"Authorization": f"Bearer {other.json()['access_token']}"}).status_code == 404


def test_rebooking_blocked_once_vehicle_assigned(client, as_role, journey):  # noqa: F811
    fleet = client.get("/orgs/directory", params={"kind": "fleet"}, headers=as_role("fpo")).json()[0]["id"]
    r = client.post(f"/shipments/{journey['shipment']['id']}/book", headers=as_role("fpo"), json={"fleet_org_id": fleet})
    assert r.status_code == 409


def test_cost_config_file_drives_the_math(monkeypatch, tmp_path):
    cfg = cost_config()
    assert cfg["transport"]["rate_per_km_ton"] > 0 and cfg["spoilage"]["crop_sensitivity"]["Tomato"] == 1.0
    f = tmp_path / "r.toml"
    f.write_text((open(cfg["_path"]).read()).replace("base_pct_per_hour = 0.4", "base_pct_per_hour = 0.8"))
    monkeypatch.setenv("RECOMMENDER_CONFIG", str(f))
    cost_config.cache_clear()
    try:
        assert abs(spoilage_pct(1, 30) - 0.8) < 1e-9
    finally:
        cost_config.cache_clear()


def test_baseline_endpoint(client, as_role, db):
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    start = date.today() - timedelta(days=120)
    for i in range(120):
        db.add(Price(mandi_id=kolar.id, commodity="Tomato", variety="Local", grade="FAQ", date=start + timedelta(days=i),
                     modal_price=1000 + 5 * i, source="agmarknet"))
    db.commit()
    b = client.get("/forecasts/baseline", params={"mandi_id": kolar.id}, headers=as_role("buyer")).json()
    assert b["model"] == "naive" and len(b["horizons"]) == 4 and not b["is_synthetic"]
    for h in b["horizons"]:
        assert h["p10"] <= h["p50"] <= h["p90"]
    assert client.get("/prices", params={"mandi_id": kolar.id}, headers=as_role("farmer")).json()[-1]["modal_price"] == 1595
    assert client.get("/forecasts/baseline", params={"mandi_id": 99999}, headers=as_role("buyer")).status_code == 404
