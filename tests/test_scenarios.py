"""V3-2 scenario simulator: COUNTERFACTUAL ESTIMATES, two separate channels, never touching `forecasts`."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from agripulse_api.models import Forecast, Mandi
from agripulse_ml.scenarios import assumptions as B, label, scenario_config

LABEL = "COUNTERFACTUAL ESTIMATE — not a validated causal model"


def test_label_is_the_rule_22_text():
    assert label() == LABEL


def test_no_shock_no_shift():
    assert B.multipliers(B.rainfall_supply_changes(0.0)) == {"central": 1.0, "low": 1.0, "high": 1.0}
    assert B.multipliers({"central": 0.0, "all": [0.0]}) == {"central": 1.0, "low": 1.0, "high": 1.0}


def test_directions_ranges_and_sources():
    rain = [B.multipliers(B.rainfall_supply_changes(d)) for d in (0.2, 0.5, 1.0)]
    assert all(m["low"] <= m["central"] <= m["high"] for m in rain)
    assert 1 < rain[0]["central"] < rain[1]["central"] < rain[2]["central"]  # less rain -> less supply -> higher price
    ban = B.multipliers({"central": B.export_share(), "all": [B.export_share()]})
    assert ban["low"] <= ban["central"] <= ban["high"] < 1  # exports stay home -> lower price
    assert 0.004 < B.export_share() < 0.005  # 96.8 kt / 208.19 lakh t (WITS 2023, PIB 2023-24)
    for m in rain + [ban]:  # the band never narrows in relative terms
        s = B.shift(1000, 1200, 1500, m)
        assert s["p10"] <= s["p50"] <= s["p90"] and s["p90"] / s["p10"] >= 1500 / 1000 - 1e-12
    cfg = scenario_config()
    assert cfg["demand"]["elasticity_central"] == -0.721 and "rbi.org.in" in cfg["demand"]["source"]
    assert cfg["rainfall_failure"]["unreplaced_rain_share_source"] == "UNSOURCED"  # said out loud, not hidden


def test_a_deficit_only_hits_arrivals_after_the_harvest_lag():
    s, e = date(2026, 7, 1), date(2026, 7, 31)
    a, b = B.affected_window(s, e)
    assert a == s + timedelta(days=60) and b == e + timedelta(days=140)


# ---------------------------------------------------------------- API


def _snapshot(db):
    db.expire_all()
    return sorted((f.id, f.mandi_id, f.issue_date, f.horizon_weeks, f.p10, f.p50, f.p90, f.p10_raw, f.p90_raw,
                   f.spike_prob, f.model_name, f.calibration, f.data_provenance) for f in db.scalars(select(Forecast)))


def _forecasts(db):
    from tests.test_tracking import seed_forecasts

    seed_forecasts(db)


def test_rainfall_failure_shifts_only_the_region_and_never_touches_forecasts(client, as_role, db):
    _forecasts(db)
    before = _snapshot(db)
    today = date.today()
    body = {"scenario": "rainfall_failure",
            "params": {"districts": ["Kolar"], "deficit": 0.5, "start": str(today - timedelta(days=90)),
                       "end": str(today - timedelta(days=30))}}
    r = client.post("/scenarios/run", json=body, headers=as_role("policy"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["label"] == LABEL and out["data_provenance"] == "synthetic" and out["model_name"] == "lightgbm_quantile"
    assert _snapshot(db) == before  # the forecasts table is untouched, byte for byte
    kolar = {m.id for m in db.scalars(select(Mandi).where(Mandi.district == "Kolar"))}
    assert kolar and out["summary"]["affected_mandis"] == len(kolar)
    for row in out["results"]:
        for h in row["horizons"]:
            moved = h["assumption"]["p50"] != h["baseline"]["p50"]
            assert moved == (row["mandi_id"] in kolar) == h["assumption"]["applies"]
            if moved:
                assert h["assumption"]["p50"] > h["baseline"]["p50"] and h["assumption"]["p90"] >= h["baseline"]["p90"]
    # no trained model on this test server: channel A says so instead of inventing a number
    assert out["summary"]["model_available"] is False and "No trained" in out["summary"]["model_reason"]
    assert all(h["model"] is None for row in out["results"] for h in row["horizons"])
    assert out["assumptions"]["demand"]["source"].startswith("https://www.rbi.org.in")
    runs = client.get("/scenarios/runs", headers=as_role("policy")).json()
    assert runs[0]["id"] == out["id"] and runs[0]["label"] == LABEL
    assert client.get(f"/scenarios/runs/{out['id']}", headers=as_role("admin")).json()["results"]


def test_export_ban_is_tiny_nationally_and_a_user_share_is_recorded(client, as_role, db):
    _forecasts(db)
    r = client.post("/scenarios/run", json={"scenario": "export_ban", "params": {"start": str(date.today())}},
                    headers=as_role("policy")).json()
    shifts = [h["assumption"]["p50"] / h["baseline"]["p50"] - 1 for row in r["results"] for h in row["horizons"]]
    assert shifts and all(-0.006 < s < 0 for s in shifts)  # about -0.3%: exports are ~0.47% of production
    assert r["params"]["share_is_user_assumption"] is False
    u = client.post("/scenarios/run", json={"scenario": "export_ban", "params": {"start": str(date.today()), "share": 0.1}},
                    headers=as_role("policy")).json()
    assert u["params"]["share_is_user_assumption"] is True and u["assumptions"]["export_ban"]["share_used"] == 0.1


@pytest.mark.parametrize("body", [
    {"scenario": "locusts", "params": {}},
    {"scenario": "rainfall_failure", "params": {"districts": ["Atlantis"], "deficit": 0.5, "start": "2026-07-01", "end": "2026-07-31"}},
    {"scenario": "rainfall_failure", "params": {"districts": ["Kolar"], "deficit": 1.5, "start": "2026-07-01", "end": "2026-07-31"}},
    {"scenario": "rainfall_failure", "params": {"districts": ["Kolar"], "deficit": 0.5, "start": "2026-07-31", "end": "2026-07-01"}},
    {"scenario": "export_ban", "params": {"start": "2026-10-01", "share": 0.9}},
    {"scenario": "export_ban", "params": {"start": "soon"}},
])
def test_bad_parameters_are_refused(client, as_role, db, body):
    _forecasts(db)
    assert client.post("/scenarios/run", json=body, headers=as_role("policy")).status_code == 422


def test_policy_and_admin_only(client, as_role, db):
    _forecasts(db)
    body = {"scenario": "export_ban", "params": {"start": str(date.today())}}
    assert client.post("/scenarios/run", json=body, headers=as_role("farmer")).status_code == 403
    assert client.get("/scenarios", headers=as_role("trader")).status_code == 403
    types = client.get("/scenarios", headers=as_role("policy")).json()
    assert types["label"] == LABEL and {s["id"] for s in types["scenarios"]} == {"rainfall_failure", "export_ban"}


def test_model_channel_with_a_trained_model(db, tmp_path, monkeypatch):
    """Channel A on a real (small, synthetic-trained) display model: a zero perturbation changes nothing; a total rain
    failure over the model's 30-day view gives ratios; the forecasts table is still untouched."""
    from agripulse_api.config import get_settings
    from agripulse_api.models import User
    from agripulse_ml import predict, synthetic, train
    from agripulse_ml.scenarios.run import run
    from agripulse_ml.synthetic import SYNTH_MANDIS

    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path))
    monkeypatch.setattr(synthetic, "SYNTH_MANDIS", SYNTH_MANDIS[:3])
    synthetic.load_into_db(db, start=date(2024, 1, 1), end=date(2025, 8, 31))
    train.train(db, allow_synthetic=True, n_folds=2)
    predict.predict_latest(db)
    before = _snapshot(db)
    user = db.scalar(select(User).where(User.role == "policy"))
    last = date(2025, 8, 31)
    zero = run(db, user, "rainfall_failure", {"districts": ["Kolar"], "deficit": 0.0, "start": str(last - timedelta(days=20)),
                                              "end": str(last)})
    assert zero.summary["model_available"] is True
    for row in zero.results:
        for h in row["horizons"]:
            assert h["model"] == h["baseline"]  # nothing perturbed -> identical
    dry = run(db, user, "rainfall_failure", {"districts": ["Kolar", "Bengaluru", "Chikkaballapur"], "deficit": 1.0,
                                             "start": str(last - timedelta(days=29)), "end": str(last)})
    assert dry.summary["model_available"] and dry.summary["model_p50_shift_pct"]["1"] is not None
    assert all(h["model"]["p10"] <= h["model"]["p50"] <= h["model"]["p90"] for r in dry.results for h in r["horizons"])
    assert _snapshot(db) == before
    assert isinstance(dry.summary["channels_disagree_weeks"], list)
    old = run(db, user, "rainfall_failure", {"districts": ["Kolar"], "deficit": 1.0, "start": "2024-03-01", "end": "2024-03-31"})
    assert any("outside the model" in n for n in old.summary["model_notes"])
