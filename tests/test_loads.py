"""V3-1: shared truckloads and return loads (core + FPO / fleet-owner proposals)."""
import copy
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from agripulse_api.decisions import Lot, MandiOption, Problem, Vehicle, evaluate, optimize
from agripulse_api.decisions.loads import candidate_loads, optimize_loads, order_pickups
from agripulse_api.decisions.model import approximate_distance
from agripulse_api.decisions.returns import add_return_loads
from agripulse_api.decisions.sample import scenario
from agripulse_api.models import AuditLog, GeofenceEvent, LoadProposal, Lot as LotRow, Shipment, Trip
from agripulse_api.supply import cost_config
from tests.test_tracking import journey, seed_forecasts  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[1]
MANDIS = [MandiOption(1, "Kolar", 13.137, 78.129, 850, 1000, 1200, 31.0, 200.0),
          MandiOption(2, "Bengaluru", 12.975, 77.565, 950, 1150, 1400, 31.0, 200.0),
          MandiOption(3, "Chintamani", 13.40, 78.057, 800, 950, 1150, 31.0, 200.0),
          MandiOption(4, "Chitradurga", 14.23, 76.40, 800, 1000, 1250, 33.0, 150.0)]


def prob(lots, trucks, cfg=None):
    return Problem(lots, trucks, MANDIS, approximate_distance, cfg or cost_config())


def test_pickup_order_is_exact():
    # three pickups on a line towards the mandi: farthest first is the only sensible order
    m = MANDIS[1]
    lots = [Lot("near", 13.00, 77.62, 1), Lot("far", 13.20, 77.95, 1), Lot("mid", 13.10, 77.80, 1)]
    order, km = order_pickups(prob(lots, []), lots, m)
    assert [x.id for x in order] == ["far", "mid", "near"]


@pytest.mark.parametrize("density", ["medium", "dense_tight"])
def test_sharing_is_never_worse_than_one_lot_per_truck_on_its_own_objective(density):
    """Single lots keep V3-0's options, so any V3-0 plan is still available to V3-1."""
    lots, trucks = scenario(np.random.default_rng([4, 2, 1, 101]), density, clustered=True)
    p = prob(lots, trucks)
    p50 = {m.id: m.p50 for m in MANDIS}
    v30, v31 = optimize(p, "p50"), optimize_loads(p, "p50")[0]
    assert v30.status == v31.status == "OPTIMAL"
    e30, e31 = evaluate(p, v30, p50), evaluate(p, v31, p50)
    assert e31["net_value"] >= e30["net_value"] - 1
    assert e31["violations_total"] == 0
    if density == "medium":  # (the dense draw's belt is mostly out of spoilage reach of these 4 mandis)
        assert e31["shared_loads"] > 0  # clustered lots do get shared
    # every truck carries at most its capacity (evaluate raises otherwise) and shared days fit the driver day
    assert e31["driver_day_over"] <= e30["driver_day_over"]


def test_each_lot_spoils_from_its_own_pickup():
    lots = [Lot("first", 13.25, 78.05, 2), Lot("second", 13.20, 78.10, 2)]
    c = copy.deepcopy(cost_config())
    p = prob(lots, [Vehicle("t", 5, 13.26, 78.04)], c)
    r = p.route(p.vehicles[0], [(MANDIS[0], lots)])
    assert r["clock"]["first"] > r["clock"]["second"] > 0  # the first lot rides longer (incl. the loading stop)
    stop = c["consolidation"]["loading_minutes_per_stop"]
    leg = p.leg(13.25, 78.05, 13.20, 78.10)[1]
    assert r["clock"]["first"] - r["clock"]["second"] == pytest.approx(leg + stop)


def test_no_neighbours_within_radius_means_no_shared_loads():
    c = copy.deepcopy(cost_config())
    c["consolidation"]["cluster_radius_km"] = 0.0
    lots, trucks = scenario(np.random.default_rng(1), "medium", clustered=True)
    assert all(len(x) == 1 for x in candidate_loads(prob(lots, trucks, c), 16))


def test_return_loads_only_take_waiting_lots_within_the_day_and_mandi_room():
    lots, trucks = scenario(np.random.default_rng([2, 0, 2, 100]), "dense_tight")
    p = prob(lots, trucks)
    p50 = {m.id: m.p50 for m in MANDIS}
    first = optimize(p, "p50")
    both = add_return_loads(p, first)
    extra = [a for a in both.assignments if a.trip == 2]
    assert extra and {a.lot_id for a in extra} <= set(first.unserved)
    assert len({a.vehicle_id for a in extra}) == len(extra)  # one return job per truck
    assert {a.vehicle_id for a in extra} <= {a.vehicle_id for a in first.assignments}  # only trucks that delivered
    e0, e1 = evaluate(p, first, p50), evaluate(p, both, p50)
    assert e1["violations_total"] == 0 and e1["net_value"] > e0["net_value"] and e1["return_trips"] == len(extra)
    for vid in {a.vehicle_id for a in extra}:
        v = next(t for t in trucks if t.id == vid)
        legs = sorted((a for a in both.assignments if a.vehicle_id == vid), key=lambda a: (a.trip, a.seq))
        trips = [(next(m for m in MANDIS if m.id == legs[0].mandi_id), [lots[legs[0].lot_id]]),
                 (next(m for m in MANDIS if m.id == legs[-1].mandi_id), [lots[legs[-1].lot_id]])]
        assert p.route(v, trips)["minutes"] <= p.max_driver_minutes + 1e-6


# ---------------------------------------------------------------- product


@pytest.fixture()
def v31_on(tmp_path, monkeypatch):
    text = (ROOT / "config" / "recommender.toml").read_text()
    text = re.sub(r"(\[consolidation\][^\[]*?)enabled = false", r"\1enabled = true", text, flags=re.S)
    text = re.sub(r"(\[return_loads\][^\[]*?)enabled = false", r"\1enabled = true", text, flags=re.S)
    f = tmp_path / "recommender.toml"
    f.write_text(text)
    monkeypatch.setenv("RECOMMENDER_CONFIG", str(f))
    cost_config.cache_clear()
    yield
    cost_config.cache_clear()


def _lots(client, as_role, db, points, tons=1.5):
    from tests.test_tracking import org_id

    fpo = org_id(db, "Kolar Tomato Growers FPO (demo)")
    ids = []
    for lat, lon in points:
        r = client.post("/lots", headers=as_role("farmer"), json={"quantity_tons": tons, "grade": "Local",
                        "pickup_label": "test", "pickup_lat": lat, "pickup_lon": lon, "fpo_org_id": fpo})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def test_switched_off_by_default(client, as_role, db):
    assert client.get("/loads/config", headers=as_role("fpo")).json() == {
        "consolidation": cost_config()["consolidation"]["enabled"], "return_loads": cost_config()["return_loads"]["enabled"],
        "study": "docs/optimizer-results.md"}
    if not cost_config()["consolidation"]["enabled"]:
        assert client.post("/loads/plan", headers=as_role("fpo")).status_code == 409


def test_fpo_plans_shared_loads_and_accepting_creates_the_shipments(client, as_role, db, v31_on):
    seed_forecasts(db)
    ids = _lots(client, as_role, db, [(13.20, 78.02), (13.22, 78.04), (13.19, 78.05)])
    r = client.post("/loads/plan", headers=as_role("fpo"))
    assert r.status_code == 201, r.text
    prop = r.json()
    assert prop["kind"] == "consolidation" and prop["status"] == "proposed" and prop["data_provenance"] == "synthetic"
    shared = [L for L in prop["loads"] if L["shared"]]
    assert shared and prop["est_saving"] > 0 and prop["totals"]["trucks_used"] < prop["baseline"]["trucks_used"]
    assert all(L["tons"] <= L["truck_tons"] for L in prop["loads"])
    assert client.post(f"/loads/proposals/{prop['id']}/accept", headers=as_role("fleet_owner")).status_code in (403, 404)
    ok = client.post(f"/loads/proposals/{prop['id']}/accept", headers=as_role("fpo"))
    assert ok.status_code == 200 and ok.json()["status"] == "accepted"
    db.expire_all()
    for L in prop["loads"]:
        sh_ids = {db.get(LotRow, i).shipment_id for i in L["lot_ids"]}
        assert len(sh_ids) == 1 and None not in sh_ids  # each load became one shipment
        sh = db.get(Shipment, sh_ids.pop())
        assert sh.mandi_id == L["mandi_id"] and {x.status for x in sh.lots} == {"grouped"}
    assert {i for L in prop["loads"] for i in L["lot_ids"]} <= set(ids)
    assert client.post(f"/loads/proposals/{prop['id']}/accept", headers=as_role("fpo")).status_code == 409  # once
    audit = db.scalars(select(AuditLog).where(AuditLog.entity == "proposal", AuditLog.entity_id == prop["id"])).all()
    assert [a.to_state for a in audit] == ["accepted"]


def test_a_proposal_whose_lots_changed_is_stale(client, as_role, db, v31_on):
    seed_forecasts(db)
    ids = _lots(client, as_role, db, [(13.20, 78.02), (13.21, 78.03)])
    prop = client.post("/loads/plan", headers=as_role("fpo")).json()
    kolar = next(L["mandi_id"] for L in prop["loads"])
    client.post("/shipments", headers=as_role("fpo"), json={"mandi_id": kolar, "lot_ids": [ids[0]]})  # grouped by hand
    r = client.post(f"/loads/proposals/{prop['id']}/accept", headers=as_role("fpo"))
    assert r.status_code == 409 and "out of date" in r.json()["detail"]
    db.expire_all()
    assert db.get(LoadProposal, prop["id"]).status == "stale" and db.get(LotRow, ids[1]).shipment_id is None


def test_proposals_are_tenant_scoped_and_can_be_rejected(client, as_role, db, v31_on):
    seed_forecasts(db)
    _lots(client, as_role, db, [(13.20, 78.02), (13.21, 78.03)])
    prop = client.post("/loads/plan", headers=as_role("fpo")).json()
    from tests.test_tracking import org_id

    other = LoadProposal(kind="consolidation", org_id=org_id(db, "Hebbal Haulage (demo)"), created_by=1, payload={"loads": []})
    db.add(other)
    db.commit()
    assert client.post(f"/loads/proposals/{other.id}/reject", headers=as_role("fpo")).status_code == 404
    assert all(p["id"] != other.id for p in client.get("/loads/proposals", headers=as_role("fpo")).json())
    assert client.get("/loads/proposals", headers=as_role("farmer")).status_code == 403
    r = client.post(f"/loads/proposals/{prop['id']}/reject", json={"reason": "farmers want separate trucks"},
                    headers=as_role("fpo"))
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["reject_reason"].startswith("farmers")


def test_fleet_gets_a_return_load_for_a_truck_that_delivered_today(client, as_role, db, journey, v31_on):
    trip = db.get(Trip, journey["trip"]["id"])
    now = datetime.now(timezone.utc)
    trip.status, trip.consent_given_at, trip.started_at = "in_progress", now, now
    db.add(GeofenceEvent(trip_id=trip.id, event="reached_mandi", occurred_at=now, lat=trip.mandi.lat, lon=trip.mandi.lon))
    db.commit()
    # a second shipment, booked with the same fleet, lots near the mandi the truck just reached
    from tests.test_tracking import org_id

    lot_ids = _lots(client, as_role, db, [(13.15, 78.12)])
    sh = client.post("/shipments", headers=as_role("fpo"), json={"mandi_id": trip.mandi_id, "lot_ids": lot_ids}).json()
    client.post(f"/shipments/{sh['id']}/book", headers=as_role("fpo"), json={"fleet_org_id": org_id(db, "Hebbal Haulage (demo)")})
    r = client.post("/fleet/return-loads", headers=as_role("fleet_owner"))
    assert r.status_code == 201, r.text
    prop = r.json()
    [m] = prop["matches"]
    assert m["trip_id"] == trip.id and m["shipment_id"] == sh["id"] and m["empty_km_saved"] > 0 and prop["est_saving"] > 0
    assert client.post("/fleet/return-loads", headers=as_role("fpo")).status_code == 403
    ok = client.post(f"/loads/proposals/{prop['id']}/accept", headers=as_role("fleet_owner"))
    assert ok.status_code == 200, ok.text
    db.expire_all()
    new = db.scalar(select(Trip).where(Trip.shipment_id == sh["id"]))
    assert new.vehicle_id == trip.vehicle_id and new.driver_id == trip.driver_id and new.status == "assigned"
    audit = db.scalar(select(AuditLog).where(AuditLog.entity == "trip", AuditLog.entity_id == new.id))
    assert audit.details["via"] == "v3-1 return-load proposal" and audit.details["after_trip"] == trip.id


def test_v31_switches_follow_the_pre_registered_verdict():
    """[consolidation] / [return_loads] enabled must equal what the committed V3-1 study says."""
    import pandas as pd

    from agripulse_ml.consolidation_study import switch_decision

    df = pd.read_csv(ROOT / "docs" / "results" / "consolidation-synthetic.csv")
    assert sorted(df["seed"].unique()) == list(range(1, 9)) and set(df["layout"]) == {"spread", "clustered"}
    assert (df[df["method"] != "rule"]["violations_total"] == 0).all()  # no optimizer variant breaks a hard limit
    verdict = switch_decision(df)["enable"]
    cfg = cost_config()
    assert cfg["consolidation"]["enabled"] == verdict and cfg["return_loads"]["enabled"] == verdict
