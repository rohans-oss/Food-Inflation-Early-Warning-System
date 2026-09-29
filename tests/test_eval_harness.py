from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agripulse_api.models import EvalResult, ModelRun
from agripulse_api.provenance import worst
from agripulse_ml.eval import RESULT_COLUMNS, FoldSpec, NotEnoughHistory, make_folds, run, split
from agripulse_ml.eval.metrics import interval_metrics, pinball, spike_metrics
from agripulse_ml.eval.tracking import log_mlflow, record
from agripulse_ml.features import build_features
from agripulse_ml.models import NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive
from agripulse_ml.synthetic import SYNTH_MANDIS, generate

SPEC = FoldSpec(min_train_days=300, step_days=28, n_folds=3)


@pytest.fixture(scope="module")
def feat():
    p, w, a = generate(date(2023, 1, 1), date(2025, 3, 31), seed=5, mandis=SYNTH_MANDIS[:3])
    ids = {n: i + 1 for i, n in enumerate(p.mandi.unique())}
    for d in (p, w, a):
        d["mandi_id"] = d.mandi.map(ids)
    return build_features(p[["mandi_id", "date", "price"]], w[["mandi_id", "date", "precip_mm", "tmax_c"]],
                          a[["mandi_id", "date", "tonnes"]])


# ---------------------------------------------------------------- metrics, checked by hand


def test_pinball_by_hand():
    # y=100: under-predicting by 10 at q=0.9 costs 0.9*10; over-predicting by 10 at q=0.1 costs 0.9*10
    assert pinball(np.array([100.0]), np.array([90.0]), 0.9)[0] == pytest.approx(9.0)
    assert pinball(np.array([100.0]), np.array([110.0]), 0.1)[0] == pytest.approx(9.0)
    assert pinball(np.array([100.0]), np.array([110.0]), 0.9)[0] == pytest.approx(1.0)


def test_interval_metrics_by_hand():
    y = np.array([100.0, 200.0])
    m = interval_metrics(y, p10=np.array([90.0, 210.0]), p50=np.array([100.0, 220.0]), p90=np.array([110.0, 230.0]))
    assert m["coverage_p10_p90_pct"] == 50.0  # first inside, second below p10
    assert m["mape_p50_pct"] == pytest.approx((0 + 20 / 200) / 2 * 100)
    # p50 pinball: |0|*.5 and 20*.5 -> mean 5
    assert m["pinball_p50"] == pytest.approx(5.0)
    assert m["pinball_mean"] == pytest.approx((m["pinball_p10"] + m["pinball_p50"] + m["pinball_p90"]) / 3)


def test_spike_metrics_by_hand():
    truth = np.array([1, 1, 0, 0, 0, 0])
    prob = np.array([0.9, 0.2, 0.7, 0.1, 0.1, 0.1])  # 1 hit, 1 miss, 1 false alarm, 3 correct rejections
    m = spike_metrics(truth, prob, 0.5)
    assert m["spike_recall"] == 0.5 and m["spike_precision"] == 0.5
    assert m["spike_false_alarm_rate"] == pytest.approx(1 / 4)
    assert m["spike_events"] == 2


# ---------------------------------------------------------------- folds: no leakage


def test_folds_never_leak(feat):
    f = add_seasonal_naive(feat)
    folds = make_folds(f["date"], SPEC)
    assert 1 <= len(folds) <= SPEC.n_folds
    for fold in folds:
        train, test = split(f, fold, SPEC)
        # every training LABEL was observable before the cutoff
        assert train[SPEC.target_date_col].max() < fold.cutoff
        assert test["date"].min() >= fold.cutoff and test["date"].max() < fold.test_end
    # folds are contiguous and ordered
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.cutoff


def test_not_enough_history():
    with pytest.raises(NotEnoughHistory):
        make_folds(pd.Series(pd.date_range("2025-01-01", periods=200)), FoldSpec())


# ---------------------------------------------------------------- results table + provenance


def test_results_table_schema_and_provenance(feat):
    r = run(feat, {"naive": NaiveForecaster, "seasonal_naive": SeasonalNaiveForecaster}, "prices+weather",
            "synthetic", spec=SPEC, prepare=add_seasonal_naive)
    assert list(r.results.columns) == RESULT_COLUMNS
    assert set(r.results["data_provenance"]) == {"synthetic"} and r.data_provenance == "synthetic"
    assert set(r.results["mandi"]) == {"ALL", "1", "2", "3"}
    assert set(r.results["horizon"]) == {"1", "2", "3", "4", "spike_14d"}
    needed = {"pinball_p10", "pinball_p50", "pinball_p90", "pinball_mean", "mape_p50_pct", "coverage_p10_p90_pct",
              "spike_recall", "spike_precision", "spike_false_alarm_rate", "spike_brier"}
    assert needed <= set(r.results["metric_name"])
    assert set(r.results["feature_set"]) == {"prices+weather"}


def test_mixed_provenance_takes_the_worst(feat):
    prov = {1: "real", 2: "real_partial", 3: "real"}
    r = run(feat, {"naive": NaiveForecaster}, "prices", prov, spec=SPEC)
    by_mandi = r.results.groupby("mandi")["data_provenance"].first().to_dict()
    assert by_mandi == {"1": "real", "2": "real_partial", "3": "real", "ALL": "real_partial"}
    assert r.data_provenance == "real_partial"
    assert worst("real", "synthetic", "real_partial") == "synthetic"


def test_provenance_is_required_and_validated(feat):
    with pytest.raises(ValueError):
        run(feat, {"naive": NaiveForecaster}, "prices", "made_up", spec=SPEC)
    with pytest.raises(ValueError):
        run(feat, {"naive": NaiveForecaster}, "prices", {1: "real"}, spec=SPEC)  # mandis 2, 3 missing
    with pytest.raises(ValueError):
        run(feat, {"naive": NaiveForecaster}, "", "synthetic", spec=SPEC)


def test_record_and_mlflow(feat, db, tmp_path, monkeypatch):
    r = run(feat, {"naive": NaiveForecaster}, "prices", "synthetic", spec=SPEC)
    monkeypatch.setenv("MLFLOW_DISABLE", "0")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    pytest.importorskip("mlflow")
    mlflow_id = log_mlflow(r, purpose="test")
    assert mlflow_id
    record(db, r, purpose="test", mlflow_run_id=mlflow_id)
    mr = db.scalar(select(ModelRun).where(ModelRun.run_id == r.run_id))
    assert mr.data_provenance == "synthetic" and mr.feature_set == "prices" and mr.mlflow_run_id == mlflow_id
    n = len(db.scalars(select(EvalResult).where(EvalResult.run_id == r.run_id)).all())
    assert n == len(r.results)
    import mlflow

    got = mlflow.get_run(mlflow_id)
    assert got.data.tags["data_provenance"] == "synthetic"
    assert got.data.tags["provenance_label"].startswith("SYNTHETIC")
    assert "naive.h1.pinball_mean" in got.data.metrics


def test_admin_eval_runs_endpoint(feat, db, client, as_role):
    r = run(feat, {"naive": NaiveForecaster}, "prices", "synthetic", spec=SPEC)
    record(db, r, purpose="test")
    out = client.get("/admin/eval-runs", headers=as_role("admin")).json()
    assert out[0]["run_id"] == r.run_id and out[0]["provenance_label"].startswith("SYNTHETIC")
    assert client.get("/admin/eval-runs", headers=as_role("policy")).status_code == 403
