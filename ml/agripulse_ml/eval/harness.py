"""Shared walk-forward evaluation harness. Same code for synthetic and real data; only the
`data_provenance` stamp differs.

    run = harness.run(feat, {"naive": NaiveForecaster, ...}, feature_set="prices+weather",
                      data_provenance="synthetic")
    run.results   # long table: model_name, feature_set, horizon, mandi, data_provenance, metric_name, metric_value

A model is any object with fit(train_df) -> self and predict(test_df) -> {(h, "p10"|"p50"|"p90"): log-ratio
array, "spike_prob": array}. Predictions are log(price[t+7h] / price[t]); the harness converts to Rs/quintal.
`data_provenance` may be one string, or a {mandi_id: provenance} dict when mandis differ (real vs real_partial);
the pooled "ALL" row then carries the worst of them.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from agripulse_api.provenance import check, worst

from .folds import FoldSpec, NotEnoughHistory, make_folds, split
from .metrics import QUANTILES, interval_metrics, spike_metrics

RESULT_COLUMNS = ["model_name", "feature_set", "horizon", "mandi", "data_provenance", "metric_name", "metric_value"]
HORIZONS = (1, 2, 3, 4)
SPIKE_HORIZON = "spike_14d"


@dataclass
class EvalRun:
    run_id: str
    model_names: list[str]
    feature_set: str
    data_provenance: str  # overall (worst across mandis)
    spec: FoldSpec
    alert_probability: float
    folds: list[dict]
    data_range: tuple[str, str]
    n_mandis: int
    predictions: pd.DataFrame
    results: pd.DataFrame
    started_at: datetime
    finished_at: datetime
    extra: dict = field(default_factory=dict)

    def pooled(self) -> pd.DataFrame:
        """ALL-mandi rows only, as a model x (horizon, metric) table (handy for reports)."""
        r = self.results[self.results["mandi"] == "ALL"]
        return r.pivot_table(index=["model_name", "horizon"], columns="metric_name", values="metric_value", aggfunc="first")


def _prov_map(data_provenance, mandis) -> dict:
    if isinstance(data_provenance, str):
        check(data_provenance)
        return {m: data_provenance for m in mandis}
    missing = set(mandis) - set(data_provenance)
    if missing:
        raise ValueError(f"data_provenance missing for mandis {sorted(missing)[:5]}")
    return {m: check(data_provenance[m]) for m in mandis}


def run(
    feat: pd.DataFrame,
    models: dict,
    feature_set: str,
    data_provenance,
    spec: FoldSpec | None = None,
    alert_probability: float = 0.5,
    per_mandi: bool = True,
    prepare=None,
) -> EvalRun:
    """`prepare(feat) -> feat` runs once before folding (e.g. adding seasonal-naive columns)."""
    spec = spec or FoldSpec()
    started = datetime.now(timezone.utc)
    if not feature_set:
        raise ValueError("feature_set is required (e.g. 'prices+weather')")
    mandis = sorted(feat["mandi_id"].unique().tolist())
    prov = _prov_map(data_provenance, mandis)
    if prepare is not None:
        feat = prepare(feat)
    folds = make_folds(feat["date"], spec)

    frames, fold_log = [], []
    for fold in folds:
        train, test = split(feat, fold, spec)
        if not len(test) or len(train) < spec.min_train_rows:
            continue
        for name, factory in models.items():
            p = factory().fit(train).predict(test)
            fr = test[["mandi_id", "date", "price", "spike"]].copy()
            fr["model_name"], fr["fold"] = name, fold.index
            for h in HORIZONS:
                fr[f"y_h{h}"] = test[f"target_h{h}"].to_numpy()
                for q in QUANTILES:
                    fr[f"q{int(q * 100)}_h{h}"] = p[(h, f"p{int(q * 100)}")]
            fr["spike_prob"] = p["spike_prob"]
            frames.append(fr)
        fold_log.append({"fold": fold.index, "cutoff": str(fold.cutoff.date()), "test_end": str(fold.test_end.date()),
                         "train_rows": len(train), "test_rows": len(test),
                         "train_max_target_date": str(train[spec.target_date_col].max().date())})
    if not frames:
        raise NotEnoughHistory("No fold had enough training rows")
    preds = pd.concat(frames, ignore_index=True)
    results = score(preds, feature_set, prov, alert_probability, per_mandi)
    return EvalRun(
        run_id=uuid.uuid4().hex, model_names=list(models), feature_set=feature_set,
        data_provenance=worst(*prov.values()), spec=spec, alert_probability=alert_probability, folds=fold_log,
        data_range=(str(feat["date"].min().date()), str(feat["date"].max().date())), n_mandis=len(mandis),
        predictions=preds, results=results, started_at=started, finished_at=datetime.now(timezone.utc),
    )


def _score_group(g: pd.DataFrame, alert_probability: float) -> list[tuple[str, str, float | None]]:
    rows = []
    for h in HORIZONS:
        ok = g[f"y_h{h}"].notna()
        gg = g[ok]
        base = gg["price"].to_numpy()
        y = base * np.exp(gg[f"y_h{h}"].to_numpy())
        qp = [base * np.exp(gg[f"q{int(q * 100)}_h{h}"].to_numpy()) for q in QUANTILES]
        for k, v in interval_metrics(y, *qp).items():
            rows.append((str(h), k, v))
    s = g[g["spike"].notna()]
    for k, v in spike_metrics(s["spike"].to_numpy(), s["spike_prob"].to_numpy(), alert_probability).items():
        rows.append((SPIKE_HORIZON, k, v))
    return rows


def score(preds: pd.DataFrame, feature_set: str, prov: dict, alert_probability: float, per_mandi: bool = True) -> pd.DataFrame:
    out = []
    for model, g in preds.groupby("model_name", sort=False):
        overall = worst(*(prov[m] for m in g["mandi_id"].unique()))
        for h, k, v in _score_group(g, alert_probability):
            out.append((model, feature_set, h, "ALL", overall, k, v))
        if per_mandi:
            for mid, gm in g.groupby("mandi_id"):
                for h, k, v in _score_group(gm, alert_probability):
                    out.append((model, feature_set, h, str(mid), prov[mid], k, v))
    df = pd.DataFrame(out, columns=RESULT_COLUMNS)
    df["metric_value"] = df["metric_value"].astype(float)  # None -> NaN
    return df


def legacy_metrics(results: pd.DataFrame) -> dict:
    """The V1 backtest.json 'metrics' shape, derived from the long table (keeps V1 screens working)."""
    r = results[results["mandi"] == "ALL"]
    out = {}
    for model, g in r.groupby("model_name", sort=False):
        v = {(h, k): x for h, k, x in g[["horizon", "metric_name", "metric_value"]].itertuples(index=False)}
        nz = lambda x, d: None if x is None or pd.isna(x) else round(float(x), d)  # noqa: E731
        out[model] = {
            "by_horizon": {f"h{h}": {"pinball": nz(v.get((str(h), "pinball_mean")), 2),
                                     "mape_pct": nz(v.get((str(h), "mape_p50_pct")), 2),
                                     "coverage_p10_p90_pct": nz(v.get((str(h), "coverage_p10_p90_pct")), 1),
                                     "n": int(v.get((str(h), "n"), 0))} for h in HORIZONS},
            "spike": {"events": int(v.get((SPIKE_HORIZON, "spike_events"), 0)),
                      "recall": nz(v.get((SPIKE_HORIZON, "spike_recall")), 3),
                      "precision": nz(v.get((SPIKE_HORIZON, "spike_precision")), 3),
                      "false_alarm_rate": nz(v.get((SPIKE_HORIZON, "spike_false_alarm_rate")), 3),
                      "brier": nz(v.get((SPIKE_HORIZON, "spike_brier")), 4)},
        }
    return out
