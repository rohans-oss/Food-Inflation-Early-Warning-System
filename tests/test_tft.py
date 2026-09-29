"""V2-2 TFT: leakage, harness contract, calibration, config-driven real/synthetic switch, forecast flag.

Uses a deliberately tiny TFT (1 epoch, hidden 4) so the suite stays fast; the numbers are meaningless,
only the plumbing is tested. Skipped if pytorch-forecasting is not installed (`pip install -e .[tft]`).
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pytorch_forecasting")

from agripulse_api.models import Forecast, Mandi, Price  # noqa: E402
from agripulse_api.provenance import REAL, REAL_PARTIAL, SYNTHETIC  # noqa: E402
from agripulse_ml.eval import run  # noqa: E402
from agripulse_ml.features.inputs import Inputs  # noqa: E402
from agripulse_ml.features.store import build_table  # noqa: E402
from agripulse_ml.tft.experiment import run_experiment  # noqa: E402
from agripulse_ml.tft.forecast import write_forecasts  # noqa: E402
from agripulse_ml.tft.model import TFTForecaster, spike_probability  # noqa: E402

TINY = {
    "data_provenance": "synthetic", "feature_set": "prices+weather", "synthetic_seeds": [7], "n_folds": 1,
    "write_forecasts": False, "max_encoder_length": 28, "hidden_size": 4, "hidden_continuous_size": 2,
    "attention_head_size": 1, "dropout": 0.0, "learning_rate": 0.03, "batch_size": 64, "max_epochs": 1,
    "limit_train_batches": 3, "early_stopping_patience": 1, "validation_days": 30, "calibration_days": 60, "seed": 1,
}


@pytest.fixture(scope="module")
def table():
    return build_table(Inputs.from_synthetic(seed=7, start=date(2024, 1, 1), end=date(2025, 3, 31), n_mandis=2))


@pytest.fixture(scope="module")
def fitted(table):
    df = table.df
    cutoff = pd.Timestamp("2025-02-01")
    history = df[df["date"] < cutoff]
    lag = pd.Timedelta(days=table.label_lag_days)
    train = history[history["target_date_h4"] + lag < cutoff]
    m = TFTForecaster(TINY, table.columns, calibrate=True, label_lag_days=table.label_lag_days)
    return m.fit(train, history=history), cutoff


def test_spike_probability_approximation():
    base = np.log(np.array([1000.0, 1000.0, 1000.0]))
    steps = 14
    def q(p10, p50, p90):
        return np.tile(np.log([p10, p50, p90]), (steps, 1))
    qs = np.stack([q(800, 900, 1000), q(1100, 1400, 1800), q(900, 1250, 1400)])
    p = spike_probability(qs, base, threshold_pct=30)
    assert p[0] == 0.0                   # whole distribution below +30%
    assert p[1] > 0.5                    # median above +30%
    assert 0.1 < p[2] < 0.5              # threshold between p50 and p90
    assert spike_probability(qs, base, threshold_pct=50)[1] < p[1]  # higher bar, lower probability


def test_predictions_ignore_context_after_issue_date(fitted, table):
    """Leakage: for a test row issued on t, changing any context row dated after t changes nothing."""
    model, cutoff = fitted
    df = table.df
    t = cutoff + pd.Timedelta(days=5)
    rows = df[df["date"] == t]
    context = df[df["date"] < cutoff + pd.Timedelta(days=28)]
    before = model.predict(rows, context=context)

    tampered = context.copy()
    after = tampered["date"] > t
    num = [c for c in tampered.columns if tampered[c].dtype.kind == "f" and not c.startswith("target")]
    tampered.loc[after, num] = tampered.loc[after, num] * 3 + 7
    tampered.loc[after, "price"] = tampered.loc[after, "price"] * 5
    again = model.predict(rows, context=tampered)
    for k in before:
        np.testing.assert_allclose(before[k], again[k], rtol=1e-6, atol=1e-8)

    # power check: the same tampering ON or before t does move the forecast
    tampered2 = context.copy()
    upto = tampered2["date"] <= t
    tampered2.loc[upto, "price"] = tampered2.loc[upto, "price"] * 5
    moved = model.predict(rows, context=tampered2)
    assert not np.allclose(before[(1, "p50")], moved[(1, "p50")])


def test_quantiles_ordered_and_calibration_widens_or_narrows(fitted, table):
    model, cutoff = fitted
    rows = table.df[(table.df["date"] >= cutoff) & (table.df["date"] < cutoff + pd.Timedelta(days=14))]
    context = table.df[table.df["date"] < cutoff + pd.Timedelta(days=14)]
    p = model.predict(rows, context=context)
    raw = TFTForecaster(TINY, table.columns, calibrate=False, cache=model.cache,
                        label_lag_days=table.label_lag_days)
    raw.state = model.state
    r = raw.predict(rows, context=context)
    for h in (1, 2, 3, 4):
        assert (p[(h, "p10")] <= p[(h, "p50")] + 1e-9).all() and (p[(h, "p50")] <= p[(h, "p90")] + 1e-9).all()
        np.testing.assert_allclose(p[(h, "p50")], r[(h, "p50")])  # CQR only moves the outer quantiles
        assert set(model.state["offsets"]) == {1, 2, 3, 4}
    assert ((p["spike_prob"] >= 0) & (p["spike_prob"] <= 1)).all()


def test_harness_gives_sequence_models_only_pre_cutoff_history(table):
    seen = []

    class Spy:
        uses_history = True

        def fit(self, train, history=None):
            seen.append(("fit", train["date"].max(), history["date"].max()))
            return self

        def predict(self, test, context=None):
            seen.append(("predict", test["date"].min(), context["date"].max(), test["date"].max()))
            z = np.zeros(len(test))
            out = {(h, q): z for h in (1, 2, 3, 4) for q in ("p10", "p50", "p90")}
            out["spike_prob"] = z
            return out

    r = run(table.df, {"spy": Spy}, table.feature_set, table.mandi_provenance,
            spec=table.fold_spec(n_folds=2, min_train_days=300))
    fits = [s for s in seen if s[0] == "fit"]
    preds = [s for s in seen if s[0] == "predict"]
    for fold, (_, train_max, hist_max), (_, test_min, ctx_max, test_max) in zip(r.folds, fits, preds):
        cutoff = pd.Timestamp(fold["cutoff"])
        assert hist_max < cutoff and train_max < cutoff
        assert test_min >= cutoff and ctx_max <= test_max


# ------------------------------------------------------------------ real data switch (config only)

def _real_prices(db, days_by_mandi: dict[int, int]):
    today = date.today()
    rng = np.random.default_rng(3)
    for mid, days in days_by_mandi.items():
        p = 1500.0
        for k in range(days, 0, -1):
            p = max(300.0, p * float(np.exp(rng.normal(0, 0.04))))
            db.add(Price(mandi_id=mid, commodity="Tomato", date=today - timedelta(days=k), modal_price=round(p),
                         min_price=round(p * 0.8), max_price=round(p * 1.2), source="agmarknet"))
    db.commit()


@pytest.fixture()
def real_db(db):
    mids = [m.id for m in db.query(Mandi).order_by(Mandi.id).limit(2)]
    _real_prices(db, {mids[0]: 500, mids[1]: 250})  # mandi 2 has < 365 days: real_partial
    return db, mids


def test_config_switch_to_real_needs_no_code_change(real_db):
    db, mids = real_db
    cfg = dict(TINY, data_provenance="real")
    out = run_experiment(cfg, db=db, n_folds=1, fold_overrides={"min_train_days": 300})
    res = out["results"]
    assert set(res["model_name"]) == {"seasonal_naive", "naive", "lightgbm_v1", "tft_raw", "tft_cqr"}
    per = res[res["mandi"] != "ALL"].groupby("mandi")["data_provenance"].first().to_dict()
    assert per[str(mids[0])] == REAL
    assert per.get(str(mids[1]), REAL_PARTIAL) == REAL_PARTIAL
    assert out["data_provenance"] in (REAL, REAL_PARTIAL)
    assert SYNTHETIC not in set(res["data_provenance"])
    assert out["timings"][0]["seed"] is None


def test_tft_forecasts_written_only_behind_flag(real_db, client):
    db, mids = real_db
    cfg = dict(TINY, data_provenance="real")
    assert write_forecasts(db, cfg)["written"] == 0
    assert db.query(Forecast).filter(Forecast.model_name == "tft").count() == 0

    out = write_forecasts(db, dict(cfg, write_forecasts=True))
    assert out["written"] == 8 and out["model_name"] == "tft"
    rows = db.query(Forecast).filter(Forecast.model_name == "tft").all()
    prov = {f.mandi_id: f.data_provenance for f in rows}
    assert prov[mids[0]] == REAL and prov[mids[1]] == REAL_PARTIAL
    assert all(not f.trained_on_synthetic and f.p10 <= f.p50 <= f.p90 for f in rows)

    # users still see the [display] model only: TFT rows alone produce no forecast block
    from agripulse_api.routers.prices import forecast_block

    assert forecast_block(db, mids[0]) is None
    assert forecast_block(db, mids[0], model="tft")["model"] == "tft"
