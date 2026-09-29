from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agripulse_api.models import Forecast
from agripulse_ml.evaluate import BacktestConfig, walk_forward
from agripulse_ml.features import FEATURES, build_features
from agripulse_ml.synthetic import SYNTH_MANDIS, generate


@pytest.fixture(scope="module")
def small():
    p, w, a = generate(date(2024, 1, 1), date(2025, 8, 31), seed=3, mandis=SYNTH_MANDIS[:4])
    ids = {n: i for i, n in enumerate(p.mandi.unique())}
    for d in (p, w, a):
        d["mandi_id"] = d.mandi.map(ids)
    return p[["mandi_id", "date", "price"]], w[["mandi_id", "date", "precip_mm", "tmax_c"]], a[["mandi_id", "date", "tonnes"]]


def test_features_do_not_look_ahead(small):
    p, w, a = small
    full = build_features(p, w, a)
    cutoff = pd.Timestamp("2025-03-01")
    part = build_features(p[p.date <= cutoff], w[w.date <= cutoff], a[a.date <= cutoff])
    key = ["mandi_id", "date"]
    merged = full.merge(part, on=key, suffixes=("", "_cut"))
    merged = merged[merged.date <= cutoff]
    for f in FEATURES:
        x, y = merged[f].to_numpy(dtype=float), merged[f + "_cut"].to_numpy(dtype=float)
        both = ~(np.isnan(x) | np.isnan(y))
        assert np.allclose(x[both], y[both]), f"feature {f} changes when future data is removed"


def test_targets_and_spike_label(small):
    p, w, a = small
    f = build_features(p, w, a, spike_threshold_pct=30)
    g = f[f.mandi_id == 0].set_index("date")
    t = g.index[100]
    later = g.loc[t + pd.Timedelta(days=7), "price"]
    assert np.isclose(g.loc[t, "target_h1"], np.log(later / g.loc[t, "price"]))
    window = g.loc[t + pd.Timedelta(days=1) : t + pd.Timedelta(days=14), "price"]
    assert g.loc[t, "spike"] == float(window.max() / g.loc[t, "price"] - 1 > 0.30)


def test_walk_forward_no_leakage_and_quantile_order(small):
    p, w, a = small
    feat = build_features(p, w, a)
    rep = walk_forward(feat, BacktestConfig(min_train_days=300, n_folds=2, step_days=28))
    assert len(rep["folds"]) >= 2
    for fold in rep["folds"]:
        assert fold["train_rows"] > 0
    for model in ("naive", "seasonal_naive", "lightgbm_quantile"):
        m = rep["metrics"][model]
        for h in ("h1", "h2", "h3", "h4"):
            assert m["by_horizon"][h]["pinball"] > 0
            assert 0 <= m["by_horizon"][h]["coverage_p10_p90_pct"] <= 100


def test_train_predict_end_to_end_marks_synthetic(db, tmp_path, monkeypatch):
    from agripulse_api.config import get_settings
    from agripulse_ml import predict, synthetic, train

    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path))
    # Keep it small: 3 seeded mandis, ~20 months.
    monkeypatch.setattr(synthetic, "SYNTH_MANDIS", SYNTH_MANDIS[:3])
    synthetic.load_into_db(db, start=date(2024, 1, 1), end=date(2025, 8, 31))
    with pytest.raises(SystemExit):
        train.train(db, allow_synthetic=False)  # no real data -> refuses
    rep = train.train(db, allow_synthetic=True, n_folds=2)
    assert rep["trained_on_synthetic"] is True
    assert (tmp_path / "backtest.json").exists() and (tmp_path / "lgb_h4_p90.txt").exists()
    out = predict.predict_latest(db)
    assert out["synthetic"] and out["written"] == 3 * 4
    rows = db.scalars(select(Forecast)).all()
    for f in rows:
        assert f.p10 <= f.p50 <= f.p90 and 0 <= f.spike_prob <= 1 and f.trained_on_synthetic
        assert f.data_provenance == "synthetic"


def test_train_explains_short_history(db, tmp_path, monkeypatch):
    from agripulse_api.config import get_settings
    from agripulse_ml import synthetic, train

    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path))
    monkeypatch.setattr(synthetic, "SYNTH_MANDIS", SYNTH_MANDIS[:2])
    synthetic.load_into_db(db, start=date(2025, 6, 1), end=date(2025, 12, 31))  # ~7 months
    with pytest.raises(SystemExit, match="Not enough history"):
        train.train(db, allow_synthetic=True, n_folds=2)
