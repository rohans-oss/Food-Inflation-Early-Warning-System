"""V3-0: decisions. The OR-Tools optimizer vs the OLD V1 rule, scored by one evaluator (rule 21)."""
import copy
from pathlib import Path

import numpy as np
import pytest

from agripulse_api.decisions import Lot, MandiOption, Problem, Vehicle, evaluate, optimize, rule_plan
from agripulse_api.decisions.model import approximate_distance
from agripulse_api.decisions.rule import v1_choices
from agripulse_api.decisions.sample import scenario
from agripulse_api.supply import cost_config

ROOT = Path(__file__).resolve().parents[1]


def cfg(**over):
    c = copy.deepcopy(cost_config())
    for k, v in over.items():
        sec, key = k.split("__")
        c[sec][key] = v
    return c


def toy(c=None):
    near = MandiOption(1, "Near", 13.14, 78.13, 900, 1000, 1100, 30.0, typical_daily_tons=200)
    rich = MandiOption(2, "Rich but small", 12.97, 77.56, 1100, 1300, 1500, 30.0, typical_daily_tons=40)  # cap 10 t
    lots = [Lot(i, 13.2, 78.0, 4.0) for i in range(5)]
    trucks = [Vehicle(i, 5.0, 13.2, 78.0) for i in range(4)]
    return Problem(lots, trucks, [near, rich], approximate_distance, c or cost_config())


def test_optimizer_respects_every_constraint_and_the_rule_is_caught_breaking_them():
    p = toy()
    rule, opt = rule_plan(p), optimize(p, "p50")
    prices = {1: 1000, 2: 1300}
    r, o = evaluate(p, rule, prices), evaluate(p, opt, prices)
    # V1 sends every lot to the best-priced mandi: 16 t into a mandi that can absorb 10 t
    assert r["violations_mandi_over"] == 1 and r["violations_mandi_over_tons"] == pytest.approx(6.0)
    assert o["violations_total"] == 0 and opt.status == "OPTIMAL"
    # 5 lots of 4 t, 4 trucks of 5 t: one lot can't ship either way; no truck is overloaded
    assert r["lots_unserved"] == o["lots_unserved"] == 1
    # honest: the rule earns MORE here, because overloading isn't priced (docs/optimizer-results.md)
    assert r["net_value"] > o["net_value"]


def test_spoilage_cap_is_hard_for_the_optimizer_only():
    c = cfg(constraints__max_spoilage_pct=0.5)  # tiny cap: only very short trips allowed
    p = toy(c)
    far = [m for m in p.mandis if p.spoilage(p.lots[0], m) > 0.5]
    assert far, "test needs a mandi beyond the cap"
    o = evaluate(p, optimize(p, "p50"), {1: 1000, 2: 1300})
    r = evaluate(p, rule_plan(p), {1: 1000, 2: 1300})
    assert o["violations_spoilage_lots"] == 0 and r["violations_spoilage_lots"] > 0


@pytest.mark.parametrize("density", ["sparse", "medium", "dense_tight"])
def test_optimizer_is_never_worse_than_the_rule_on_its_own_objective_when_nothing_binds(density):
    """Optimality check: with caps switched off, the rule's plan is one of the optimizer's options, so the optimizer's
    planned value can't be lower."""
    c = cfg(constraints__max_spoilage_pct=1e9, constraints__mandi_extra_share=1e9, optimizer__max_mandis_per_lot=1000)
    rng = np.random.default_rng(3)
    mandis = [MandiOption(i, f"M{i}", lat, lon, 900 + 40 * i, 1000 + 40 * i, 1200 + 40 * i, 32.0, 200.0)
              for i, (lat, lon) in enumerate([(12.97, 77.56), (13.14, 78.13), (13.40, 78.06), (14.23, 76.40),
                                              (15.85, 74.50), (12.30, 76.64)])]
    lots, trucks = scenario(rng, density)
    p = Problem(lots, trucks, mandis, approximate_distance, c)
    p50 = {m.id: m.p50 for m in mandis}
    rule, opt = evaluate(p, rule_plan(p), p50), evaluate(p, optimize(p, "p50"), p50)
    assert opt["net_value"] >= rule["net_value"] - 1  # rounding to whole rupees
    fixed = optimize(p, "p50", fixed_mandis=v1_choices(p))
    assert {a.mandi_id for a in fixed.assignments} <= {m.id for m in v1_choices(p).values()}


def test_evaluator_scores_at_the_prices_given_not_the_forecast():
    p = toy()
    plan = optimize(p, "p50")
    at_forecast = evaluate(p, plan, {1: 1000, 2: 1300})
    crash = evaluate(p, plan, {1: 500, 2: 650})
    assert crash["net_value"] < at_forecast["net_value"]  # same plan, worse outcome: decisions are graded ex post
    a = plan.assignments[0]
    lot, m, v = p.lots[a.lot_id], {x.id: x for x in p.mandis}[a.mandi_id], p.vehicles[a.vehicle_id]
    hand = 1000 * 4 * 10 * (1 - p.spoilage(lot, m) / 100) - p.trip_cost(v, lot, m) if m.id == 1 else None
    if hand is not None:
        row = next(r for r in at_forecast["assignments"] if r["lot"] == a.lot_id)
        assert row["net"] == round(hand)


def test_risk_averse_objective_plans_on_p10():
    p = toy()
    o10 = optimize(p, "p10")
    assert o10.method == "optimizer_p10" and o10.status == "OPTIMAL"
    with pytest.raises(ValueError):
        optimize(p, "p99")


# ---------------------------------------------------------------- API


def _with_default(tmp_path, monkeypatch, which):
    import re

    text = re.sub(r'^default = "\w+"', f'default = "{which}"', (ROOT / "config" / "recommender.toml").read_text(), flags=re.M)
    f = tmp_path / "recommender.toml"
    f.write_text(text)
    monkeypatch.setenv("RECOMMENDER_CONFIG", str(f))
    cost_config.cache_clear()


@pytest.fixture()
def optimizer_default(tmp_path, monkeypatch):
    _with_default(tmp_path, monkeypatch, "optimizer")
    yield
    cost_config.cache_clear()


@pytest.fixture()
def rule_default(tmp_path, monkeypatch):
    _with_default(tmp_path, monkeypatch, "rule")
    yield
    cost_config.cache_clear()


def _forecasts(db):
    from tests.test_tracking import seed_forecasts

    seed_forecasts(db)


def test_farmer_endpoint_uses_the_configured_recommender(client, as_role, db, optimizer_default):
    _forecasts(db)
    r = client.get("/recommend/best-mandi", params={"lat": 13.2, "lon": 78.02, "tons": 4}, headers=as_role("farmer"))
    body = r.json()
    assert r.status_code == 200 and body["recommender"] == "optimizer" and body["vehicle_assumption"]["capacity_tons"] == 5.0
    assert all("feasible" in x for x in body["ranked"]) and body["ranked"][0]["feasible"]
    ranks = [(not x["feasible"], -x["net_value"]["p50"]) for x in body["ranked"]]
    assert ranks == sorted(ranks)  # feasible first, then by net value


def test_the_v1_rule_stays_callable_as_a_fallback(client, as_role, db, rule_default):
    _forecasts(db)
    body = client.get("/recommend/best-mandi", params={"lat": 13.2, "lon": 78.02, "tons": 4}, headers=as_role("farmer")).json()
    assert body["recommender"] == "rule" and "feasible" not in body["ranked"][0]


def test_trucks_already_on_the_road_count_against_a_mandis_room(client, as_role, db, optimizer_default, monkeypatch):
    from agripulse_api.decisions import service

    _forecasts(db)
    top = client.get("/recommend/best-mandi", params={"lat": 13.2, "lon": 78.02, "tons": 4},
                     headers=as_role("farmer")).json()["ranked"][0]
    monkeypatch.setattr(service, "typical_daily_arrivals", lambda db, mid: {"tons": 40.0, "days": 28, "is_synthetic": True})
    monkeypatch.setattr(service, "in_transit", lambda db: {top["mandi_id"]: {"tons_in_transit": 8.0}})  # room: 10 - 8 = 2 t
    body = client.get("/recommend/best-mandi", params={"lat": 13.2, "lon": 78.02, "tons": 4}, headers=as_role("farmer")).json()
    row = next(x for x in body["ranked"] if x["mandi_id"] == top["mandi_id"])
    assert row["feasible"] is False and "can take about 2 t" in row["why_not"][0]
    assert body["ranked"][0]["mandi_id"] != top["mandi_id"]


def test_admin_compares_both_recommenders_on_the_same_batch(client, as_role, db):
    _forecasts(db)
    r = client.post("/admin/recommenders/compare", json={"density": "medium", "seed": 2}, headers=as_role("admin"))
    body = r.json()
    assert r.status_code == 200 and [x["method"] for x in body["results"]] == ["rule", "optimizer", "optimizer_p10"]
    assert body["lots_are_simulated"] is True and body["data_provenance"] == "synthetic" and body["n_lots"] == 20
    assert 0 <= body["mandis_with_known_room"] <= body["mandis"]
    from agripulse_api.decisions.service import default_recommender

    assert client.get("/admin/recommenders", headers=as_role("admin")).json()["default"] == default_recommender()
    assert client.post("/admin/recommenders/compare", json={"density": "huge"}, headers=as_role("admin")).status_code == 422
    assert client.post("/admin/recommenders/compare", json={}, headers=as_role("fpo")).status_code == 403


def test_default_recommender_follows_the_pre_registered_switch_rule():
    """The config default must be what the committed V3-0 study says (docs/results/optimizer-synthetic.csv)."""
    import pandas as pd

    from agripulse_api.decisions.service import default_recommender
    from agripulse_ml.decision_study import switch_decision

    df = pd.read_csv(ROOT / "docs" / "results" / "optimizer-synthetic.csv")
    assert sorted(df["seed"].unique()) == list(range(1, 9)) and set(df["density"]) == {"sparse", "medium", "dense_tight"}
    assert (df[df["method"] == "optimizer"]["violations_total"] == 0).all()  # the optimizer never breaks a hard limit
    assert default_recommender() == switch_decision(df)["verdict"]
