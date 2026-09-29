"""V2-3 experiment: graph models vs non-graph models, same table, same folds, per draw and per generator.

    python -m agripulse_ml.graph.experiment                 # [gnn] in config/models.toml
    python -m agripulse_ml.graph.experiment --record        # + DB (model_runs / eval_results) + MLflow
    python -m agripulse_ml.graph.experiment --generators random --seeds 7 --folds 1   # quick look

Models (all on the same rows / folds / metrics):
  seasonal_naive, naive        baselines (rule 10)
  lightgbm_v1                  V1 LightGBM on the non-graph columns (prices + weather + calendar)
  lightgbm_graph               the same LightGBM + the `graph` feature group
  gru_nograph                  the GNN network with no edges (control: isolates message passing)
  gnn                          GRU + 2 graph-convolution layers over distance + correlation + flow-ESTIMATE edges

Generators (synthetic only): "random" is the pinned baseline, where distance carries no information by
construction; "distance" is the PLANTED SIGNAL positive control (spikes spread from Kolar by road distance).
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
from .gnn import GraphFitCache, GraphForecaster

ROOT = Path(__file__).resolve().parents[3]
MODEL_ORDER = ["seasonal_naive", "naive", "lightgbm_v1", "lightgbm_graph", "gru_nograph", "gnn"]
PLANTED = "PLANTED SIGNAL (positive control): spikes spread by road distance; not evidence about real prices"


def model_factories(table, cfg: dict) -> tuple[dict, GraphFitCache]:
    cache = GraphFitCache()
    base = [c for c in table.feature_columns if not c.startswith("gr_")]
    full = list(table.feature_columns)
    return {
        "seasonal_naive": SeasonalNaiveForecaster,
        "naive": NaiveForecaster,
        "lightgbm_v1": lambda: LightGBMQuantileForecaster(features=base),
        "lightgbm_graph": lambda: LightGBMQuantileForecaster(features=full),
        "gru_nograph": lambda: GraphForecaster(cfg, table, use_graph=False, cache=cache),
        "gnn": lambda: GraphForecaster(cfg, table, use_graph=True, cache=cache),
    }, cache


def inputs_for(cfg: dict, db=None, seed=None, generator: str = "random") -> Inputs:
    if cfg["data_provenance"] == "synthetic":
        return Inputs.from_synthetic(seed=seed, propagation=generator)
    if cfg["data_provenance"] == "real":
        if db is None:
            raise ValueError("data_provenance = 'real' needs a database session")
        return Inputs.from_db(db, synthetic=False)
    raise ValueError(f"[gnn] data_provenance must be 'synthetic' or 'real', got {cfg['data_provenance']!r}")


def run_experiment(cfg: dict | None = None, db=None, record: bool = False, seeds=None, generators=None,
                   n_folds: int | None = None, fold_overrides: dict | None = None) -> dict:
    cfg = dict(cfg or models_config()["gnn"])
    n_folds = n_folds or int(cfg["n_folds"])
    synthetic = cfg["data_provenance"] == "synthetic"
    draws = (seeds or cfg["synthetic_seeds"]) if synthetic else [None]
    gens = (generators or cfg["generators"]) if synthetic else ["real"]
    runs, timings, frames = [], [], []
    for gen in gens:
        for seed in draws:
            table = build_table(inputs_for(cfg, db, seed, gen if synthetic else "random"), cfg["feature_set"])
            models, cache = model_factories(table, cfg)
            t0 = time.time()
            r = run(table.df, models, table.feature_set, table.mandi_provenance,
                    spec=table.fold_spec(n_folds=n_folds, **(fold_overrides or {})), prepare=add_seasonal_naive)
            r.extra = {"generator": gen, "seed": seed, "wall_seconds": round(time.time() - t0, 1),
                       "planted_signal": gen == "distance",
                       "fits": [{"model": "gnn" if k[2] else "gru_nograph", "cutoff": str(k[0].date()),
                                 "epochs": v["epochs"], "fit_seconds": v["fit_seconds"], "graph_edges": v["n_edges"],
                                 "offsets": {str(h): [round(x, 4) for x in o] for h, o in v["offsets"].items()}}
                                for k, v in cache.fits.items()],
                       "graph_edges_last_snapshot": table.graphs[-1].edges.groupby("edge_type").size().to_dict()
                       if table.graphs else {}}
            runs.append(r)
            timings.append(r.extra)
            frames.append(r.results.assign(seed=seed, generator=gen))
            if record and db is not None:
                from ..eval.tracking import log_mlflow, record as record_run

                purpose = "v2_graph_planted" if gen == "distance" else "v2_graph"
                mid = log_mlflow(r, purpose=purpose)
                record_run(db, r, purpose=purpose, mlflow_run_id=mid, notes=json.dumps(r.extra))
    return {"runs": runs, "results": pd.concat(frames, ignore_index=True), "timings": timings,
            "data_provenance": runs[0].data_provenance}


def summary(results: pd.DataFrame) -> dict:
    out = {}
    for gen, g in results[results["mandi"] == "ALL"].groupby("generator"):
        mean = g.groupby(["model_name", "horizon", "metric_name"])["metric_value"].mean().unstack("metric_name")
        pin = g[g["metric_name"] == "pinball_mean"].pivot_table(index=["seed", "horizon"], columns="model_name",
                                                                values="metric_value")
        gains = {m: (100 * (1 - pin[m] / pin["naive"])).unstack("horizon") for m in pin.columns if m != "naive"}
        out[gen] = {"mean": mean, "gain_vs_naive": gains}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--generators", default="")
    ap.add_argument("--folds", type=int, default=None)
    args = ap.parse_args()
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    cfg = models_config()["gnn"]
    db = None
    if args.record or cfg["data_provenance"] == "real":
        from agripulse_api.db import SessionLocal

        db = SessionLocal()
    out = run_experiment(db=db, record=args.record, seeds=[int(s) for s in args.seeds.split(",") if s] or None,
                         generators=[g for g in args.generators.split(",") if g] or None, n_folds=args.folds)
    res_dir = ROOT / "docs" / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    tag = out["data_provenance"]
    out["results"].to_csv(res_dir / f"graph-{tag}.csv", index=False)
    (res_dir / f"graph-{tag}-timings.json").write_text(json.dumps(out["timings"], indent=2))
    print(LABEL[tag])
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        for gen, s in summary(out["results"]).items():
            print(f"\n===== generator: {gen}" + (f"  [{PLANTED}]" if gen == "distance" else ""))
            print(s["mean"][["pinball_mean", "coverage_p10_p90_pct"]].dropna(how="all").round(2))
            for m, g in s["gain_vs_naive"].items():
                print(f"\n{m}: % better than naive (pinball), per draw\n{g.round(1)}")
    if db is not None:
        db.close()


if __name__ == "__main__":
    main()
