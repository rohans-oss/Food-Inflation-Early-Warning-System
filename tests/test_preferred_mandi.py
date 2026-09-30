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
