"""Pre-V3 B-1: model-agnostic interval calibration and the coverage check."""
import numpy as np
import pandas as pd
import pytest

from agripulse_ml.calibration import APPLIED, NOT_YET, apply_to_predictions, calibrate_stream, calibration_config
from agripulse_ml.eval.coverage import CoverageDrift, assert_coverage, coverage_check


def _stream(days=700, per_day=18, sd=0.2, lag=28, seed=1, half=0.1):
    rng = np.random.default_rng(seed)
    d = np.repeat(pd.date_range("2024-01-01", periods=days).to_numpy(), per_day)
    s = pd.DataFrame({"date": d, "y": rng.normal(0, sd, len(d))})
    s["known_on"] = s["date"] + pd.Timedelta(days=lag)
    return s.assign(horizon=4, q10=-half, q50=0.0, q90=half)  # far too narrow: the true 80% range is +-0.256


@pytest.mark.parametrize("method", ["track_record", "aci"])
def test_overconfident_ranges_are_calibrated_to_the_target(method):
    c = calibrate_stream(_stream(), method=method)
    a = c[(c["calibration"] == APPLIED) & (c["date"] >= "2025-01-01")]
    cov = ((a["y"] >= a["q10_cal"]) & (a["y"] <= a["q90_cal"])).mean()
    raw = ((a["y"] >= a["q10"]) & (a["y"] <= a["q90"])).mean()
    assert raw < 0.45 and abs(cov - 0.80) <= 0.03, (method, raw, cov)
    assert (a["q10_cal"] <= a["q50"]).all() and (a["q50"] <= a["q90_cal"]).all()


@pytest.mark.parametrize("method", ["track_record", "aci"])
def test_calibration_only_uses_outcomes_published_before_the_issue_date(method):
    s = _stream(days=500)
    t = pd.Timestamp("2025-02-01")
    base = calibrate_stream(s, method=method)
    tampered = s.copy()
    future = tampered["known_on"] >= t  # not yet published on t
    tampered.loc[future, "y"] = tampered.loc[future, "y"] * 10 + 3
    again = calibrate_stream(tampered, method=method)
    on_t = base["date"] == t
    np.testing.assert_allclose(base.loc[on_t, "q10_cal"], again.loc[on_t, "q10_cal"])
    np.testing.assert_allclose(base.loc[on_t, "q90_cal"], again.loc[on_t, "q90_cal"])
    later = base["date"] == t + pd.Timedelta(days=60)  # power check: once published, the tampering matters
    assert not np.allclose(base.loc[later, "q90_cal"], again.loc[later, "q90_cal"])


def test_short_track_record_is_not_yet_applicable_and_leaves_the_range():
    cfg = calibration_config()
    s = _stream(days=40)  # 40 days x 18 - 28 days of lag: well below min_scores for the first issue dates
    c = calibrate_stream(s, cfg)
    first = c[c["date"] == c["date"].min()]
    assert (first["calibration"] == NOT_YET).all() and (first["q10_cal"] == first["q10"]).all()
    assert (first["n_scores"] < int(cfg["calibration"]["min_scores"])).all()


def test_wraps_any_model_and_every_run_reports_coverage_and_the_check_catches_drift():
    """Model-agnostic: one wrapper calibrates naive and LightGBM harness output. Every run reports coverage gap and
    pass/fail rows. This small dataset has a price spike (Aug-Oct 2024) that NO calibrator anticipates: coverage
    drifts even after calibration, and the check must say so (tolerance from config/calibration.toml)."""
    from datetime import date

    from agripulse_api.provenance import SYNTHETIC
    from agripulse_ml.eval import run
    from agripulse_ml.eval.harness import score
    from agripulse_ml.features.inputs import Inputs
    from agripulse_ml.features.store import build_table
    from agripulse_ml.models import LightGBMQuantileForecaster, NaiveForecaster

    tb = build_table(Inputs.from_synthetic(seed=5, start=date(2022, 1, 1), end=date(2024, 12, 31), n_mandis=4),
                     "prices")
    r = run(tb.df, {"naive": NaiveForecaster, "lgbm": lambda: LightGBMQuantileForecaster(features=tb.feature_columns)},
            tb.feature_set, SYNTHETIC, spec=tb.fold_spec(n_folds=12, min_train_days=300), per_mandi=False)
    assert {"coverage_gap_pct", "coverage_within_tol"} <= set(r.results["metric_name"])  # every run reports it
    parts = []
    for m, g in r.predictions.groupby("model_name"):
        cal, stream = apply_to_predictions(g, tb.label_lag_days)
        parts.append(cal)
        assert set(stream["calibration"]) <= {APPLIED, NOT_YET} and (stream["calibration"] == APPLIED).any()
        for h in (1, 2, 3, 4):
            assert (cal[f"q10_h{h}"] <= cal[f"q50_h{h}"] + 1e-12).all() and (cal[f"q50_h{h}"] <= cal[f"q90_h{h}"] + 1e-12).all()
    after = score(pd.concat(parts), tb.feature_set, {m: SYNTHETIC for m in tb.df["mandi_id"].unique()}, 0.5, per_mandi=False)
    with pytest.raises(CoverageDrift):
        assert_coverage(after, models=["naive", "lgbm"])
    assert len(assert_coverage(after, models=["naive"], tolerance=100)) == 4  # the tolerance is configurable
    chk = coverage_check(after)
    assert set(chk.columns) >= {"coverage_pct", "gap_pct", "within_tolerance"}


def test_study_result_mean_coverage_is_on_target_after_calibration():
    """Regression guard on the committed B-1 study (docs/results/calibration-diagnosis.csv, V2-0's 8 datasets):
    V1 LightGBM's mean p10-p90 coverage across the 8 datasets is within tolerance at every horizon after the
    pre-registered calibration, and is NOT at 4 weeks before it. Re-running the study must keep this true."""
    from pathlib import Path

    tol = float(calibration_config()["calibration"]["tolerance_pct"])
    d = pd.read_csv(Path(__file__).resolve().parents[1] / "docs" / "results" / "calibration-diagnosis.csv")
    lg = d[d["model_name"] == "lightgbm_quantile"]
    assert sorted(lg["seed"].unique()) == list(range(1, 9))
    mean = lg.groupby(["method", "horizon"])["coverage_pct"].mean()
    for h in (1, 2, 3, 4):
        assert abs(mean[("track_record", h)] - 80) <= tol, h
    assert abs(mean[("before", 4)] - 80) > tol  # the uncalibrated 4-week range drifts (74.2%)


def test_live_calibration_from_stored_forecasts_and_the_api_label(db, client, as_role):
    """Live path: offsets come from this model's own stored forecasts vs published prices (real vs real), use the
    RAW range, and the forecast API says whether the displayed range is calibrated."""
    from datetime import date, timedelta

    from agripulse_api.models import Forecast, Mandi, Price
    from agripulse_ml.calibration import calibrate_range, serving_offsets

    m = db.query(Mandi).first()
    rng = np.random.default_rng(0)
    start = date(2025, 1, 1)
    for k in range(260):
        d = start + timedelta(days=k)
        db.add(Price(mandi_id=m.id, commodity="Tomato", date=d, modal_price=float(1500 * np.exp(rng.normal(0, 0.2))),
                     source="agmarknet"))
        for h in (1, 2, 3, 4):  # a model whose raw range is far too narrow (+-5% around 1500)
            db.add(Forecast(mandi_id=m.id, commodity="Tomato", issue_date=d, target_date=d + timedelta(weeks=h),
                            horizon_weeks=h, p10=1425, p50=1500, p90=1575, p10_raw=1425, p90_raw=1575, spike_prob=0.1,
                            model_name="test_model", trained_on_synthetic=False, data_provenance="real"))
    db.commit()
    off = serving_offsets(db, "test_model", synthetic=False, as_of=date(2025, 9, 1), label_lag_days=1)
    assert all(off[h]["calibration"] == APPLIED and off[h]["n_scores"] >= 100 for h in (1, 2, 3, 4))
    lo, hi = calibrate_range(1425, 1500, 1575, off[4])
    assert lo < 1300 and hi > 1700  # widened towards the true +-25% (sd 0.2 in log space)
    early = serving_offsets(db, "test_model", synthetic=False, as_of=date(2025, 1, 20), label_lag_days=1)
    assert early[4]["calibration"] == NOT_YET and calibrate_range(1425, 1500, 1575, early[4]) == (1425, 1575)
    assert serving_offsets(db, "test_model", synthetic=True, as_of=date(2025, 9, 1))[1]["calibration"] == NOT_YET

    f = db.query(Forecast).filter(Forecast.model_name == "test_model", Forecast.issue_date == date(2025, 9, 1)).all()
    for x in f:
        x.model_name, x.calibration = "lightgbm_quantile", "applied"
    db.commit()
    body = client.get(f"/forecasts/{m.id}", headers=as_role("farmer")).json()
    assert body["calibration"] == "applied" and "calibrated" in body["calibration_label"]
    assert {"p10_raw", "p90_raw", "calibration"} <= set(body["horizons"][0])
    base = client.get(f"/forecasts/baseline?mandi_id={m.id}", headers=as_role("farmer")).json()
    assert base["calibration"] == "not_needed"


def test_admin_card_shows_the_calibration_study_and_live_status(client, as_role):
    rows = client.get("/admin/v2-results", headers=as_role("admin")).json()
    b1 = [r for r in rows if r["phase"] == "B-1"]
    study = next(r for r in b1 if r["data_provenance"] == "synthetic")
    assert study["calibration"] == "applied" and study["doc"] == "docs/calibration-results.md"
    live = next(r for r in b1 if r["outcome"] == "status")
    assert live["calibration"] in {"applied", "not_yet_applicable", "partial", "none"}
