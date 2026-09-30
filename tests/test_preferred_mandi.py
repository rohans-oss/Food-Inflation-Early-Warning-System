"""A farmer can choose the mandi for a lot (from Best mandi); the FPO sees it; grouping locks it."""
from sqlalchemy import select

from agripulse_api.models import AuditLog, Mandi
from tests.test_tracking import FARM, org_id


def _lot(client, as_role, db):
    return client.post("/lots", headers=as_role("farmer"), json={
        "quantity_tons": 2, "pickup_lat": FARM[0], "pickup_lon": FARM[1],
        "fpo_org_id": org_id(db, "Kolar Tomato Growers FPO (demo)")}).json()


def test_farmer_chooses_mandi_fpo_sees_it_and_grouping_locks_it(client, as_role, db):
    lot = _lot(client, as_role, db)
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    r = client.post(f"/lots/{lot['id']}/preferred-mandi", headers=as_role("farmer"), json={"mandi_id": kolar.id})
    assert r.status_code == 200 and r.json()["preferred_mandi"] == "Kolar APMC"
    assert db.scalar(select(AuditLog).where(AuditLog.entity == "lot", AuditLog.field == "preferred_mandi"))
    fpo_view = next(x for x in client.get("/lots", headers=as_role("fpo")).json() if x["id"] == lot["id"])
    assert fpo_view["preferred_mandi_id"] == kolar.id
    assert client.post(f"/lots/{lot['id']}/preferred-mandi", headers=as_role("farmer"), json={"mandi_id": 999999}).status_code == 400
    assert client.post(f"/lots/{lot['id']}/preferred-mandi", headers=as_role("fpo"), json={"mandi_id": kolar.id}).status_code == 403
    client.post("/shipments", headers=as_role("fpo"), json={"mandi_id": kolar.id, "lot_ids": [lot["id"]]})
    assert client.post(f"/lots/{lot['id']}/preferred-mandi", headers=as_role("farmer"), json={"mandi_id": None}).status_code == 409


def test_next_steps_show_route_free_trucks_and_request_notifies_the_fpo(client, as_role, db):
    from tests.test_tracking import seed_forecasts

    seed_forecasts(db)
    lot = _lot(client, as_role, db)
    F = as_role("farmer")
    ns = client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()
    assert ns["mandi"] is None and not ns["can_request"] and not ns["done"]["mandi_chosen"]
    assert client.post(f"/lots/{lot['id']}/request-transport", headers=F).status_code == 409  # no mandi yet
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    client.post(f"/lots/{lot['id']}/preferred-mandi", headers=F, json={"mandi_id": kolar.id})
    ns = client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()
    assert ns["mandi"]["name"] == "Kolar APMC" and ns["can_request"] and ns["fpo"]
    assert ns["route"] and ns["route"]["road_km"] > 0 and ns["route"]["transport_cost"] > 0
    demo_fleet = next(f for f in ns["fleets"] if f["name"] == "Hebbal Haulage (demo)")
    assert demo_fleet["free_that_fit"] >= 1  # KA-01-XX-1234, 5 t, idle
    r = client.post(f"/lots/{lot['id']}/request-transport", headers=F)
    assert r.status_code == 200 and r.json()["transport_requested_at"]
    alerts = client.get("/alerts", headers=as_role("fpo")).json()
    assert any(a["kind"] == "transport_requested" and "Kolar APMC" in a["body"] for a in alerts)
    assert client.get(f"/lots/{lot['id']}/next-steps", headers=F).json()["done"]["transport_requested"]
    # changing the mandi resets the request
    other = db.scalar(select(Mandi).where(Mandi.name != "Kolar APMC", Mandi.lat.is_not(None)))
    client.post(f"/lots/{lot['id']}/preferred-mandi", headers=F, json={"mandi_id": other.id})
    assert client.get(f"/lots/{lot['id']}", headers=F).json()["transport_requested_at"] is None
