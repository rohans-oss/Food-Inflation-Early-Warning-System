"""V2-5 ablation: what each feature group adds to LightGBM, on the same folds, per draw.

    python -m agripulse_ml.ablation                        # synthetic: 3 draws x 4 folds  -> docs/results/ablation-synthetic.*
    python -m agripulse_ml.ablation --provenance real      # real-so-far rows in DATABASE_URL -> docs/results/ablation-real.json
    python -m agripulse_ml.ablation --record               # + model_runs / eval_results + MLflow

Models (one table, one set of folds, one metric set): seasonal_naive, naive, and LightGBM (V1 hyper-parameters) on
  lgbm_prices               price + arrival history, calendar, static
  lgbm_base                 prices + weather: the V1 feature set, the base every group is added to
  lgbm_graph                base + mandi-graph features
  lgbm_satellite            base + Sentinel-2 cropland NDVI
  lgbm_transit              base + in-transit tonnage
  lgbm_all                  every group
(names avoid brackets and '+': MLflow metric names allow neither)

Labels that matter when reading the SYNTHETIC run:
  satellite   the NDVI is REAL, the prices are SYNTHETIC. The generator's prices do not depend on real NDVI, so this row
              tests the plumbing only; any gain would be chance. Only mandis in the pilot districts have NDVI.
  transit     the trips are SIMULATED from the synthetic arrivals, so tomorrow's arrivals are in today's transit BY
              CONSTRUCTION. A gain is built in: it shows the pipeline carries the signal, not that transit data helps.
"""
import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

import pandas as pd

from agripulse_api.provenance import LABEL

from .eval import run
from .eval.folds import NotEnoughHistory
from .features.inputs import Inputs
from .features.store import build_table
from .models import LightGBMQuantileForecaster, NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive

ROOT = Path(__file__).resolve().parents[2]
SATELLITE_FILE = ROOT / "data" / "satellite" / "observations.csv"
FULL = "prices+weather+satellite+graph+transit"
SEEDS = [7, 1, 2]
N_FOLDS = 4
PREFIX = {"weather": ("wx_", "wf_"), "graph": ("gr_",), "satellite": ("sat_",), "transit": ("tr_",)}
VARIANTS = {  # name: groups on top of prices (+ calendar + static, always)
    "lgbm_prices": [],
    "lgbm_base": ["weather"],
    "lgbm_graph": ["weather", "graph"],
    "lgbm_satellite": ["weather", "satellite"],
    "lgbm_transit": ["weather", "transit"],
    "lgbm_all": ["weather", "graph", "satellite", "transit"],
}
NOTES = {
    "lgbm_satellite": "REAL NDVI x SYNTHETIC prices: plumbing check only; NDVI only for mandis in the pilot districts",
    "lgbm_transit": "SIMULATED trips built from synthetic arrivals: the signal is built in by construction",
}


def columns_for(table, groups: list[str]) -> list[str]:
    drop = tuple(p for g, ps in PREFIX.items() if g not in groups for p in ps)
    return [c for c in table.feature_columns if not c.startswith(drop)]


def satellite_obs() -> pd.DataFrame:
    if not SATELLITE_FILE.exists():
        return pd.DataFrame(columns=["district", "date", "ndvi_median", "clear_px"])
    o = pd.read_csv(SATELLITE_FILE, parse_dates=["date"])
    return o[["district", "date", "ndvi_median", "clear_px", "in_scene_px"]]


def models_for(table) -> dict:
    m = {"seasonal_naive": SeasonalNaiveForecaster, "naive": NaiveForecaster}
    for name, groups in VARIANTS.items():
        cols = columns_for(table, groups)
        m[name] = (lambda c: (lambda: LightGBMQuantileForecaster(features=c)))(cols)
    return m


def run_synthetic(seeds=None, n_folds: int = N_FOLDS, record_db=None, sat: pd.DataFrame | None = None) -> dict:
    sat = satellite_obs() if sat is None else sat
    frames, runs, meta = [], [], []
    for seed in seeds or SEEDS:
        inp = replace(Inputs.from_synthetic(seed=seed), satellite=sat)
        table = build_table(inp, FULL)
        r = run(table.df, models_for(table), table.feature_set, table.mandi_provenance,
                spec=table.fold_spec(n_folds=n_folds), prepare=add_seasonal_naive)
        districts = dict(zip(inp.mandis["mandi_id"], inp.mandis["district"]))
        covered = sorted(int(m) for m in table.df.loc[table.df["sat_ndvi_30"].notna(), "mandi_id"].unique())
        r.extra = {"seed": seed, "group_provenance": table.group_provenance, "satellite_mandis": covered,
                   "satellite_districts": sorted({districts[m] for m in covered}), "notes": NOTES}
        frames.append(r.results.assign(seed=seed))
        runs.append(r)
        meta.append(r.extra)
        if record_db is not None:
            from .eval.tracking import log_mlflow, record

            mid = log_mlflow(r, purpose="v2_ablation")
            record(record_db, r, purpose="v2_ablation", mlflow_run_id=mid, notes=json.dumps(r.extra))
    return {"results": pd.concat(frames, ignore_index=True), "meta": meta, "runs": runs,
            "data_provenance": runs[0].data_provenance}


def run_real(db) -> dict:
    """Real-so-far rows. With days of real history the harness cannot form a fold; that IS the result."""
    from agripulse_api.readiness import compute

    inp = Inputs.from_db(db, synthetic=False)
    ready = compute(db)
    summary = ready["summary"] if "summary" in ready else {}
    days = inp.prices.groupby("mandi_id")["date"].nunique() if len(inp.prices) else pd.Series(dtype=int)
    base = {"real_price_rows": int(len(inp.prices)), "mandis_with_real_prices": int(days.size),
            "max_real_price_days": int(days.max()) if days.size else 0, "readiness_summary": summary,
            "satellite_rows": int(len(inp.satellite)), "real_trips": int(len(inp.transit_trips))}
    if inp.prices.empty:
        return {"status": "not_enough_real_data", "reason": "no real price rows yet", **base}
    groups = "prices" + ("+satellite" if len(inp.satellite) else "") + ("+transit" if len(inp.transit_trips) else "")
    try:
        table = build_table(inp, groups)
        r = run(table.df, models_for(table), table.feature_set, table.mandi_provenance,
                spec=table.fold_spec(n_folds=N_FOLDS), prepare=add_seasonal_naive)
    except NotEnoughHistory as e:
        return {"status": "not_enough_real_data", "reason": str(e), **base}
    return {"status": "ran", "data_provenance": r.data_provenance, "results": r.results, **base}


def summary(results: pd.DataFrame) -> dict:
    """Pooled pinball per model x horizon: mean over draws, % better than lgbm[prices+weather] and than naive."""
    r = results[(results["mandi"] == "ALL") & (results["metric_name"] == "pinball_mean")]
    pin = r.pivot_table(index=["seed", "horizon"], columns="model_name", values="metric_value")
    vs_base = {m: (100 * (1 - pin[m] / pin["lgbm_base"])) for m in pin.columns}
    vs_naive = {m: (100 * (1 - pin[m] / pin["naive"])) for m in pin.columns}
    return {"pinball": pin, "vs_base": vs_base, "vs_naive": vs_naive}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance", choices=["synthetic", "real"], default="synthetic")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--folds", type=int, default=N_FOLDS)
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args(argv)
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    db = None
    if a.record or a.provenance == "real":
        from agripulse_api.db import SessionLocal

        db = SessionLocal()
    if a.provenance == "real":
        res = run_real(db)
        if "results" in res:
            res["results"].to_csv(out / "ablation-real.csv", index=False)
            res = {k: v for k, v in res.items() if k != "results"}
        (out / "ablation-real.json").write_text(json.dumps(res, indent=2, default=str))
        print(json.dumps(res, indent=2, default=str))
        return 0
    res = run_synthetic([int(s) for s in a.seeds.split(",") if s] or None, a.folds, record_db=db)
    res["results"].to_csv(out / "ablation-synthetic.csv", index=False)
    (out / "ablation-synthetic-meta.json").write_text(json.dumps(res["meta"], indent=2))
    s = summary(res["results"])
    print(LABEL[res["data_provenance"]])
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(s["pinball"].groupby(level="horizon").mean().round(1).T)
        for k in ("vs_base", "vs_naive"):
            print(f"\n% better than {'lgbm_base' if k == 'vs_base' else 'naive'} (mean over draws)")
            print(pd.DataFrame({m: v.groupby(level='horizon').mean() for m, v in s[k].items()}).round(1).T)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
