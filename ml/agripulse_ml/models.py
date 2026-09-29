"""Forecasters. All return log-ratio quantiles per horizon; callers turn them into prices."""
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import FEATURES, HORIZONS, QUANTILES

# Deliberately conservative: rows overlap heavily (daily issue dates, weekly targets),
# so small leaves / big min_child_samples keep the model from memorising runs.
LGB_PARAMS = dict(
    n_estimators=200,
    learning_rate=0.03,
    num_leaves=7,
    min_child_samples=300,
    subsample=0.7,
    subsample_freq=1,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    verbose=-1,
)
CALIBRATION_DAYS = 120  # most recent slice of training data held out to calibrate intervals


def _q_name(q: float) -> str:
    return f"p{int(round(q * 100))}"


class NaiveForecaster:
    """Last value carried forward; the interval is the empirical spread of past h-week log changes."""

    name = "naive"

    def fit(self, train: pd.DataFrame) -> "NaiveForecaster":
        self.resid = {h: train[f"target_h{h}"].dropna().quantile(list(QUANTILES)).values for h in HORIZONS}
        spikes = train["spike"].dropna()
        self.spike_rate = float(spikes.mean()) if len(spikes) else 0.0
        return self

    def predict(self, X: pd.DataFrame) -> dict:
        out = {}
        for h in HORIZONS:
            for q, v in zip(QUANTILES, self.resid[h]):
                out[(h, _q_name(q))] = np.full(len(X), v)
        out["spike_prob"] = np.full(len(X), self.spike_rate)
        return out


class SeasonalNaiveForecaster(NaiveForecaster):
    """Same week last year, scaled: p(t+h) = p(t) * p(t+h-364)/p(t-364).
    Needs >= 1 year of history per mandi; rows without it fall back to naive."""

    name = "seasonal_naive"

    def fit(self, train: pd.DataFrame) -> "SeasonalNaiveForecaster":
        super().fit(train)
        self.seasonal_resid = {}
        for h in HORIZONS:
            col = f"sn_h{h}"
            if col in train:
                err = (train[f"target_h{h}"] - train[col]).dropna()
                self.seasonal_resid[h] = err.quantile(list(QUANTILES)).values if len(err) > 50 else None
        return self

    def predict(self, X: pd.DataFrame) -> dict:
        out = super().predict(X)
        for h in HORIZONS:
            col = f"sn_h{h}"
            if col not in X or self.seasonal_resid.get(h) is None:
                continue
            centre = X[col].to_numpy()
            has = ~np.isnan(centre)
            for q, e in zip(QUANTILES, self.seasonal_resid[h]):
                key = (h, _q_name(q))
                out[key] = np.where(has, centre + e, out[key])
        return out


def add_seasonal_naive(feat: pd.DataFrame) -> pd.DataFrame:
    """sn_h{h} = log p(t+7h-364) - log p(t-364): last year's move over the same window."""
    feat = feat.copy()
    parts = []
    for _, g in feat.groupby("mandi_id", sort=False):
        g = g.sort_values("date")
        s = pd.Series(np.log(g["price"].to_numpy()), index=g["date"])
        for h in HORIZONS:
            then = s.reindex(g["date"] - pd.Timedelta(days=364)).to_numpy()
            then_fwd = s.reindex(g["date"] - pd.Timedelta(days=364 - 7 * h)).to_numpy()
            g[f"sn_h{h}"] = then_fwd - then
        parts.append(g)
    return pd.concat(parts).sort_index()


def _cqr_offsets(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, coverage: float = 0.8) -> tuple[float, float]:
    """Conformalized quantile regression: how far to widen (or narrow) [lo, hi] so that
    ~`coverage` of held-out outcomes fall inside. Split into lower/upper halves so a
    skewed miss pattern (tomato spikes are upward) is corrected on the right side."""
    alpha = (1 - coverage) / 2
    n = len(y)
    if n < 50:
        return 0.0, 0.0
    level = min(1.0, (1 - alpha) * (n + 1) / n)
    return float(np.quantile(lo - y, level)), float(np.quantile(y - hi, level))


@dataclass
class LightGBMQuantileForecaster:
    name: str = "lightgbm_quantile"
    params: dict = field(default_factory=lambda: dict(LGB_PARAMS))
    features: list = field(default_factory=lambda: list(FEATURES))  # V1 features; V2 passes table.feature_columns
    models: dict = field(default_factory=dict)
    offsets: dict = field(default_factory=dict)  # h -> (lower_widen, upper_widen) in log space
    spike_model: object = None
    spike_rate: float = 0.0

    def fit(self, train: pd.DataFrame) -> "LightGBMQuantileForecaster":
        FEATURES = self.features  # noqa: N806
        cut = train["date"].max() - pd.Timedelta(days=CALIBRATION_DAYS)
        fit_part, cal_part = train[train["date"] <= cut], train[train["date"] > cut]
        if len(fit_part) < 500 or len(cal_part) < 50:
            fit_part, cal_part = train, train.iloc[0:0]
        X = fit_part[FEATURES]
        for h in HORIZONS:
            y = fit_part[f"target_h{h}"]
            mask = y.notna()
            for q in QUANTILES:
                m = lgb.LGBMRegressor(objective="quantile", alpha=q, **self.params)
                m.fit(X[mask], y[mask])
                self.models[(h, _q_name(q))] = m
            cy = cal_part[f"target_h{h}"]
            cm = cy.notna()
            if cm.sum() >= 50:
                Xc = cal_part.loc[cm, FEATURES]
                lo = self.models[(h, "p10")].predict(Xc)
                hi = self.models[(h, "p90")].predict(Xc)
                self.offsets[h] = _cqr_offsets(cy[cm].to_numpy(), lo, hi)
            else:
                self.offsets[h] = (0.0, 0.0)
        s = train["spike"]
        mask = s.notna()
        self.spike_rate = float(s[mask].mean()) if mask.any() else 0.0
        if mask.sum() > 200 and 0 < s[mask].sum() < mask.sum():
            clf = lgb.LGBMClassifier(objective="binary", **self.params)  # no reweighting: keep probabilities calibrated
            clf.fit(train.loc[mask, FEATURES], s[mask].astype(int))
            self.spike_model = clf
        return self

    def _raw(self, h: int, q: str, Xf: pd.DataFrame) -> np.ndarray:
        return self.models[(h, q)].predict(Xf)

    def _spike(self, Xf: pd.DataFrame) -> np.ndarray:
        if self.spike_model is not None:
            return self.spike_model.predict_proba(Xf)[:, 1]
        return np.full(len(Xf), self.spike_rate)

    def predict(self, X: pd.DataFrame) -> dict:
        Xf = X[self.features]
        out = {}
        for h in HORIZONS:
            preds = np.column_stack([self._raw(h, _q_name(q), Xf) for q in QUANTILES])
            preds.sort(axis=1)  # enforce p10 <= p50 <= p90 (no quantile crossing)
            lo_adj, hi_adj = self.offsets.get(h, (0.0, 0.0))
            preds[:, 0] = np.minimum(preds[:, 0] - lo_adj, preds[:, 1])
            preds[:, 2] = np.maximum(preds[:, 2] + hi_adj, preds[:, 1])
            for i, q in enumerate(QUANTILES):
                out[(h, _q_name(q))] = preds[:, i]
        out["spike_prob"] = self._spike(Xf)
        return out

    def save(self, directory) -> None:
        import json
        from pathlib import Path

        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        for (h, q), m in self.models.items():
            m.booster_.save_model(str(d / f"lgb_h{h}_{q}.txt"))
        if self.spike_model is not None:
            self.spike_model.booster_.save_model(str(d / "lgb_spike.txt"))
        meta = {"spike_rate": self.spike_rate, "offsets": {str(h): v for h, v in self.offsets.items()}}
        (d / "model_meta.json").write_text(json.dumps(meta, indent=2))


class LoadedLightGBM(LightGBMQuantileForecaster):
    """Inference-only: rebuilds the forecaster from saved boosters (used by the predict job)."""

    def __init__(self, directory):
        import json
        from pathlib import Path

        super().__init__()
        d = Path(directory)
        self.boosters = {
            (h, _q_name(q)): lgb.Booster(model_file=str(d / f"lgb_h{h}_{_q_name(q)}.txt")) for h in HORIZONS for q in QUANTILES
        }
        spike = d / "lgb_spike.txt"
        self.spike_booster = lgb.Booster(model_file=str(spike)) if spike.exists() else None
        meta = json.loads((d / "model_meta.json").read_text())
        self.spike_rate = meta["spike_rate"]
        self.offsets = {int(h): tuple(v) for h, v in meta["offsets"].items()}

    def _raw(self, h, q, Xf):
        return self.boosters[(h, q)].predict(Xf)

    def _spike(self, Xf):
        if self.spike_booster is not None:
            return self.spike_booster.predict(Xf)
        return np.full(len(Xf), self.spike_rate)
