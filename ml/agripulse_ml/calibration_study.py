"""Pre-V3 B-1 study: interval calibration on the SAME 8 synthetic datasets and SAME final 8 folds as V2-0.

    python -m agripulse_ml.calibration_study          # ~15 min -> docs/results/calibration-*.csv

Datasets: eval/baseline.py synthetic_features(seed) for seeds 1-8 (V2-0's seed sweep), V1 features, V1 models.
Folds: V2-0 scored FoldSpec(n_folds=8). Calibrating from a model's own track record needs a year of past
out-of-sample forecasts, so the walk-forward is extended backwards (n_folds = 8 + WARMUP_FOLDS). The extra early folds
only build the track record; every number reported is on the final 8 folds, which are asserted to be V2-0's folds.
"before" = the model exactly as V1/V2 ship it (V1 LightGBM already includes its own 120-day conformal step).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from agripulse_api.provenance import SYNTHETIC

from .calibration import apply_to_predictions, calibration_config
from .eval import FoldSpec, run
from .eval.baseline import synthetic_features
from .eval.folds import make_folds
from .eval.harness import score
from .models import LightGBMQuantileForecaster, NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive

ROOT = Path(__file__).resolve().parents[2]
SEEDS = list(range(1, 9))
V2_0_FOLDS = 8
WARMUP_FOLDS = 13  # 13 x 28 = 364 days of out-of-sample forecasts before the first scored fold
MODELS = {"naive": NaiveForecaster, "seasonal_naive": SeasonalNaiveForecaster,
          "lightgbm_quantile": LightGBMQuantileForecaster}
METHODS = ("track_record", "aci")


def diagnose(preds: pd.DataFrame) -> pd.DataFrame:
    """Per model x horizon on the scored folds: coverage, misses below / above, median error of p50."""
    rows = []
    for m, g in preds.groupby("model_name"):
        for h in (1, 2, 3, 4):
            ok = g[f"y_h{h}"].notna()
            y, lo, md, hi = (g.loc[ok, c].to_numpy(float) for c in (f"y_h{h}", f"q10_h{h}", f"q50_h{h}", f"q90_h{h}"))
            err = np.exp(y) / np.exp(md) - 1  # actual vs p50, as a fraction of p50
            rows.append({"model_name": m, "horizon": h, "n": int(ok.sum()),
                         "coverage_pct": 100 * float(np.mean((y >= lo) & (y <= hi))),
                         "below_p10_pct": 100 * float(np.mean(y < lo)), "above_p90_pct": 100 * float(np.mean(y > hi)),
                         "p50_mape_pct": 100 * float(np.mean(np.abs(err))),
                         "p50_median_bias_pct": 100 * float(np.median(err))})
    return pd.DataFrame(rows)


def run_seed(seed: int, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    feat = synthetic_features(seed=seed)
    spec = FoldSpec(n_folds=V2_0_FOLDS + WARMUP_FOLDS)
    r = run(feat, MODELS, feature_set="prices+weather", data_provenance=SYNTHETIC, spec=spec, per_mandi=False,
            prepare=add_seasonal_naive)
    v20 = make_folds(feat["date"], FoldSpec(n_folds=V2_0_FOLDS))
    scored = r.folds[-V2_0_FOLDS:]
    assert [f["cutoff"] for f in scored] == [str(f.cutoff.date()) for f in v20], "final folds differ from V2-0's"
    first = pd.Timestamp(scored[0]["cutoff"])
    prov = {m: SYNTHETIC for m in feat["mandi_id"].unique()}
    frames, diag, status = [], [], {}
    preds = r.predictions
    for label, p in [("before", preds)] + [(m, None) for m in METHODS]:
        if p is None:
            parts = []
            for model, g in preds.groupby("model_name"):
                cal, stream = apply_to_predictions(g, spec.label_lag_days, cfg, method=label)
                parts.append(cal)
                s = stream[stream["date"] >= first]
                status[f"{label}:{model}"] = s.groupby("horizon")["calibration"].apply(
                    lambda x: round(100 * float((x == "applied").mean()), 1)).to_dict()
            p = pd.concat(parts)
        test = p[p["date"] >= first]
        frames.append(score(test, "prices+weather", prov, r.alert_probability, per_mandi=False).assign(method=label, seed=seed))
        diag.append(diagnose(test).assign(method=label, seed=seed))
    return pd.concat(frames, ignore_index=True), pd.concat(diag, ignore_index=True), {
        "seed": seed, "scored_cutoffs": [f["cutoff"] for f in scored], "applied_pct_by_horizon": status}


def main():
    cfg = calibration_config()
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    res, diag, meta = [], [], []
    for seed in SEEDS:
        a, b, c = run_seed(seed, cfg)
        res.append(a), diag.append(b), meta.append(c)
        cov = b[(b.model_name == "lightgbm_quantile")].pivot_table(index="method", columns="horizon", values="coverage_pct")
        print(f"seed {seed}: LightGBM p10-p90 coverage\n{cov.round(1)}", flush=True)
    pd.concat(res).to_csv(out / "calibration-synthetic.csv", index=False)
    pd.concat(diag).to_csv(out / "calibration-diagnosis.csv", index=False)
    (out / "calibration-meta.json").write_text(json.dumps({"config": {k: v for k, v in cfg.items() if k != "_path"},
                                                          "seeds": meta}, indent=2))
    print("SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
