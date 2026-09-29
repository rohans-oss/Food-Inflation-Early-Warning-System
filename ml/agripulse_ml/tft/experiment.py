"""V2-2 experiment: TFT vs V1 LightGBM vs naive vs seasonal naive, same table, same folds, per draw.

    python -m agripulse_ml.tft.experiment                   # data_provenance from config/models.toml [tft]
    python -m agripulse_ml.tft.experiment --record          # + DB (model_runs / eval_results) + MLflow

Config-driven (V2-2 step 4): `[tft] data_provenance = "synthetic"` runs the pinned generator for each of
`synthetic_seeds`; "real" reads non-synthetic DB rows once, and the readiness monitor stamps each mandi
real / real_partial. The code path is the same; only the Inputs differ.

Models (decision (b)): "lightgbm_v1" is the V1 LightGBM model and hyper-parameters trained on the V2 table
(same rows, targets, folds as TFT). The V1 as-shipped numbers (same-day prices) are a reference only.
"""
import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd

from agripulse_api.modelcfg import models_config
from agripulse_api.provenance import LABEL

from ..eval import run
from ..features.inputs import Inputs
from ..features.store import build_table
from ..models import LightGBMQuantileForecaster, NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive
from .model import TFTFitCache, TFTForecaster

ROOT = Path(__file__).resolve().parents[3]
MODEL_ORDER = ["seasonal_naive", "naive", "lightgbm_v1", "tft_raw", "tft_cqr"]


def model_factories(table, cfg: dict, include_tft: bool = True) -> tuple[dict, TFTFitCache]:
    cache = TFTFitCache()
    feats = table.feature_columns
    models = {
        "seasonal_naive": SeasonalNaiveForecaster,
        "naive": NaiveForecaster,
        "lightgbm_v1": lambda: LightGBMQuantileForecaster(features=feats),
    }
    if include_tft:
        models["tft_raw"] = lambda: TFTForecaster(cfg, table.columns, calibrate=False, cache=cache,
                                                  label_lag_days=table.label_lag_days)
        models["tft_cqr"] = lambda: TFTForecaster(cfg, table.columns, calibrate=True, cache=cache,
                                                  label_lag_days=table.label_lag_days)
    return models, cache


def inputs_for(cfg: dict, db=None, seed: int | None = None) -> Inputs:
    prov = cfg["data_provenance"]
    if prov == "synthetic":
        return Inputs.from_synthetic(seed=seed)
    if prov == "real":
        if db is None:
            raise ValueError("data_provenance = 'real' needs a database session")
        return Inputs.from_db(db, synthetic=False)
    raise ValueError(f"[tft] data_provenance must be 'synthetic' or 'real', got {prov!r}")


def run_experiment(cfg: dict | None = None, db=None, record: bool = False, seeds=None, n_folds: int | None = None,
                   fold_overrides: dict | None = None, include_tft: bool = True) -> dict:
    cfg = dict(cfg or models_config()["tft"])
    n_folds = n_folds or int(cfg["n_folds"])
    draws = (seeds or cfg["synthetic_seeds"]) if cfg["data_provenance"] == "synthetic" else [None]
    runs, timings = [], []
    for seed in draws:
        table = build_table(inputs_for(cfg, db, seed), cfg["feature_set"])
        models, cache = model_factories(table, cfg, include_tft)
        t0 = time.time()
        r = run(table.df, models, table.feature_set, table.mandi_provenance,
                spec=table.fold_spec(n_folds=n_folds, **(fold_overrides or {})), prepare=add_seasonal_naive)
        r.extra = {"seed": seed, "wall_seconds": round(time.time() - t0, 1),
                   "tft_fits": [{"cutoff": str(k[0].date()), "epochs": v["epochs"], "fit_seconds": v["fit_seconds"],
                                 "calibration_rows": v["n_calibration_rows"],
                                 "offsets": {str(h): [round(x, 4) for x in o] for h, o in v["offsets"].items()}}
                                for k, v in cache.fits.items()]}
        runs.append(r)
        timings.append(r.extra)
        if record and db is not None:
            from ..eval.tracking import log_mlflow, record as record_run

            mid = log_mlflow(r, purpose="v2_tft")
            record_run(db, r, purpose="v2_tft", mlflow_run_id=mid, notes=json.dumps(r.extra))
    results = pd.concat([r.results.assign(seed=r.extra["seed"]) for r in runs], ignore_index=True)
    return {"runs": runs, "results": results, "timings": timings, "data_provenance": runs[0].data_provenance}


def summary(results: pd.DataFrame) -> dict:
    """Pooled (ALL-mandi) metrics per model x horizon: mean over draws, plus each model's pinball vs naive per draw."""
    r = results[results["mandi"] == "ALL"]
    mean = r.groupby(["model_name", "horizon", "metric_name"])["metric_value"].mean().unstack("metric_name")
    pin = r[r["metric_name"] == "pinball_mean"].pivot_table(index=["seed", "horizon"], columns="model_name", values="metric_value")
    gains = {m: (100 * (1 - pin[m] / pin["naive"])).unstack("horizon") for m in pin.columns if m != "naive"}
    return {"mean": mean, "gain_vs_naive": gains}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--folds", type=int, default=None)
    args = ap.parse_args()
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    seeds = [int(s) for s in args.seeds.split(",") if s] or None
    db = None
    cfg = models_config()["tft"]
    if args.record or cfg["data_provenance"] == "real":
        from agripulse_api.db import SessionLocal

        db = SessionLocal()
    out = run_experiment(db=db, record=args.record, seeds=seeds, n_folds=args.folds)
    res_dir = ROOT / "docs" / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    tag = out["data_provenance"]
    out["results"].to_csv(res_dir / f"tft-{tag}.csv", index=False)
    (res_dir / f"tft-{tag}-timings.json").write_text(json.dumps(out["timings"], indent=2))
    s = summary(out["results"])
    print(LABEL[tag])
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(s["mean"][["pinball_mean", "coverage_p10_p90_pct", "mape_p50_pct"]].dropna(how="all").round(2))
        for m, g in s["gain_vs_naive"].items():
            print(f"\n{m}: % better than naive (pinball), per draw\n{g.round(1)}")
    if db is not None:
        db.close()


if __name__ == "__main__":
    main()
