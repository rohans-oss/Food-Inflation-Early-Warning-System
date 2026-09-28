"""Walk-forward backtest (rule 2: never random splits on time series).

Folds step through time. For a fold starting at T:
  train = rows whose *target date* for the longest horizon is < T   (no label leakage)
  test  = issue dates in [T, T + step)
Metrics per model and horizon, all computed in price space (Rs/quintal):
  pinball loss (mean over p10/p50/p90), MAPE of p50, p10-p90 coverage,
  spike recall / precision at the alert threshold, and Brier score.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import HORIZONS, QUANTILES
from .models import LightGBMQuantileForecaster, NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive


def pinball(y: np.ndarray, pred: np.ndarray, q: float) -> np.ndarray:
    d = y - pred
    return np.maximum(q * d, (q - 1) * d)


@dataclass
class BacktestConfig:
    min_train_days: int = 365
    step_days: int = 28
    n_folds: int = 8
    alert_probability: float = 0.5


def walk_forward(feat: pd.DataFrame, cfg: BacktestConfig | None = None, model_factories=None) -> dict:
    cfg = cfg or BacktestConfig()
    feat = add_seasonal_naive(feat)
    model_factories = model_factories or {
        "naive": NaiveForecaster,
        "seasonal_naive": SeasonalNaiveForecaster,
        "lightgbm_quantile": LightGBMQuantileForecaster,
    }
    dates = feat["date"].sort_values().unique()
    start, end = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    last_issue = end - pd.Timedelta(days=7 * max(HORIZONS))  # need observed targets
    first_fold = max(start + pd.Timedelta(days=cfg.min_train_days), last_issue - pd.Timedelta(days=cfg.step_days * cfg.n_folds))
    if first_fold >= last_issue:
        raise ValueError(
            f"Not enough history for a walk-forward backtest: {start.date()}..{end.date()} "
            f"(need > {cfg.min_train_days} days + {7 * max(HORIZONS)} days of targets)"
        )

    preds_all = []
    folds = []
    T = first_fold
    while T < last_issue:
        T_end = min(T + pd.Timedelta(days=cfg.step_days), last_issue)
        train = feat[feat[f"target_date_h{max(HORIZONS)}"] < T]
        test = feat[(feat["date"] >= T) & (feat["date"] < T_end)]
        if len(test) and len(train) > 200:
            for mname, factory in model_factories.items():
                model = factory().fit(train)
                p = model.predict(test)
                frame = test[["mandi_id", "date", "price", "spike"]].copy()
                frame["model"] = mname
                for h in HORIZONS:
                    frame[f"y_h{h}"] = test[f"target_h{h}"].to_numpy()
                    for q in QUANTILES:
                        frame[f"q{int(q * 100)}_h{h}"] = p[(h, f"p{int(q * 100)}")]
                frame["spike_prob"] = p["spike_prob"]
                preds_all.append(frame)
            folds.append({"start": str(T.date()), "end": str(T_end.date()), "train_rows": len(train), "test_rows": len(test)})
        T = T_end

    preds = pd.concat(preds_all, ignore_index=True)
    return {"folds": folds, "metrics": score(preds, cfg.alert_probability), "n_predictions": int(len(preds))}


def score(preds: pd.DataFrame, alert_probability: float) -> dict:
    results = {}
    for mname, g in preds.groupby("model"):
        per_h = {}
        for h in HORIZONS:
            ok = g[f"y_h{h}"].notna()
            gg = g[ok]
            base = gg["price"].to_numpy()
            y = base * np.exp(gg[f"y_h{h}"].to_numpy())
            qp = {q: base * np.exp(gg[f"q{int(q * 100)}_h{h}"].to_numpy()) for q in QUANTILES}
            pin = np.mean([pinball(y, qp[q], q).mean() for q in QUANTILES])
            mape = float(np.mean(np.abs(qp[0.5] - y) / y) * 100)
            cover = float(np.mean((y >= qp[0.1]) & (y <= qp[0.9])) * 100)
            per_h[f"h{h}"] = {"pinball": round(float(pin), 2), "mape_pct": round(mape, 2), "coverage_p10_p90_pct": round(cover, 1), "n": int(ok.sum())}
        s = g[g["spike"].notna()]
        truth = s["spike"].astype(int).to_numpy()
        prob = s["spike_prob"].to_numpy()
        alert = prob >= alert_probability
        tp = int((alert & (truth == 1)).sum())
        results[mname] = {
            "by_horizon": per_h,
            "spike": {
                "events": int(truth.sum()),
                "recall": round(tp / truth.sum(), 3) if truth.sum() else None,
                "precision": round(tp / alert.sum(), 3) if alert.sum() else None,
                "brier": round(float(np.mean((prob - truth) ** 2)), 4) if len(truth) else None,
            },
        }
    return results
