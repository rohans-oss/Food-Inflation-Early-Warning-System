"""FPO desk: add member farmers and register lots on their behalf; demo members give the FPO page something to plan."""
from sqlalchemy import select

from agripulse_api.models import AuditLog, Lot, User
from agripulse_api.seed import DEMO_MEMBERS, seed_demo_members

FARM = {"pickup_lat": 13.17, "pickup_lon": 78.05}


def test_fpo_adds_a_member_and_registers_a_lot_for_them(client, as_role, db):
    P = as_role("fpo")
    m = client.post("/fpo/members", headers=P, json={"name": "Gowramma", "phone": "+91 90000 00999"}).json()
    assert m["name"] == "Gowramma" and m["phone"] == "+919000000999" and not m["has_login"]
    assert client.post("/fpo/members", headers=P, json={"name": "Dup", "phone": "+919000000999"}).status_code == 409
    assert "Gowramma" in [x["name"] for x in client.get("/fpo/members", headers=P).json()]
    lot = client.post("/fpo/lots", headers=P, json={"farmer_id": m["id"], "crop": "tomato", "quantity_tons": 2,
                                                    "pickup_label": "Vemagal", **FARM})
    assert lot.status_code == 201 and lot.json()["crop"] == "Tomato"
    waiting = client.get("/lots", headers=P, params={"status": "registered"}).json()
    assert lot.json()["id"] in [x["id"] for x in waiting]
    a = db.scalar(select(AuditLog).where(AuditLog.entity == "lot", AuditLog.entity_id == lot.json()["id"]))
    assert a.details["registered_by"] == "fpo"
    assert client.get("/fpo/members", headers=P).json()[0]["waiting"] >= 0


def test_only_an_fpo_can_add_and_only_for_its_own_members(client, as_role, db):
    F = as_role("farmer")
    assert client.post("/fpo/members", headers=F, json={"name": "X"}).status_code == 403
    outsider = db.scalar(select(User).where(User.role == "farmer"))  # the demo farmer is not a member
    r = client.post("/fpo/lots", headers=as_role("fpo"), json={"farmer_id": outsider.id, "quantity_tons": 1, **FARM})
    assert r.status_code == 404


def test_demo_members_are_seeded_once_with_lots_waiting(db):
    assert seed_demo_members(db) == len(DEMO_MEMBERS)
    assert seed_demo_members(db) == 0
    lots = db.scalars(select(Lot).where(Lot.pickup_label.like("%(approx.)"))).all()
    assert len(lots) == len(DEMO_MEMBERS) and all(l.status == "registered" and l.org_id for l in lots)
