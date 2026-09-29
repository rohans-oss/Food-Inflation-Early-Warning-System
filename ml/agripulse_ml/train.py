"""Train, backtest and persist the tomato forecaster.

    python -m agripulse_ml.train                 # real data only
    python -m agripulse_ml.train --synthetic     # allow source='synthetic' rows (dev/demo)

Writes to MODEL_DIR (default ml/artifacts):
    lgb_h{1..4}_p{10,50,90}.txt, lgb_spike.txt, model_meta.json   final models (all data)
    backtest.json                                                  walk-forward report
Every run is recorded in the DB (model_runs + eval_results) and MLflow (MLFLOW_TRACKING_URI, default
sqlite store under mlruns/), stamped with data_provenance: synthetic / real / real_partial.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.db import SessionLocal

from agripulse_api.provenance import LABEL, SYNTHETIC
from agripulse_api.readiness import mandi_price_ready, provenance_for

from .data import load_arrivals, load_prices, load_weather
from .eval.tracking import log_mlflow, record
from .evaluate import BacktestConfig, walk_forward
from .features import FEATURES, build_features
from .models import LightGBMQuantileForecaster

MODEL_VERSION_FMT = "%Y%m%d-%H%M"


def dataset(db: Session, allow_synthetic: bool) -> tuple[pd.DataFrame, bool]:
    """Real rows only, unless there are none and synthetic is explicitly allowed.
    Synthetic and real data are never mixed in one model."""
    prices = load_prices(db)
    synthetic = False
    if prices.empty:
        if not allow_synthetic:
            raise SystemExit("No real price history yet. Backfill Agmarknet first, or pass --synthetic for a dev model.")
        synthetic = True
        prices = load_prices(db, synthetic=True)
    feat = build_features(
        prices,
        load_weather(db, synthetic=synthetic),
        load_arrivals(db, synthetic=synthetic),
        spike_threshold_pct=get_settings().spike_threshold_pct,
    )
    return feat, synthetic


def train(db: Session, allow_synthetic: bool = False, n_folds: int = 8, model_dir: str | None = None) -> dict:
    s = get_settings()
    model_dir = model_dir or s.model_dir
    feat, synthetic = dataset(db, allow_synthetic)
    if feat.empty:
        raise SystemExit("No usable price rows.")
    provenance = _provenance(db, feat, synthetic)
    try:
        report, run = walk_forward(feat, BacktestConfig(n_folds=n_folds, alert_probability=s.spike_alert_probability),
                                   data_provenance=provenance, return_run=True)
    except ValueError as exc:  # not enough history yet: say so instead of a traceback
        raise SystemExit(f"{exc}. Keep the daily Agmarknet job running, or backfill older history.")
    final = LightGBMQuantileForecaster().fit(feat)
    final.save(model_dir)
    version = datetime.now(timezone.utc).strftime(MODEL_VERSION_FMT)
    lgb_m, naive_m = report["metrics"]["lightgbm_quantile"], report["metrics"]["naive"]
    report.update(
        {
            "model_version": version,
            "trained_on_synthetic": synthetic,
            "data_provenance": run.data_provenance,
            "provenance_label": LABEL[run.data_provenance],
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "data_range": [str(feat["date"].min().date()), str(feat["date"].max().date())],
            "mandis": int(feat["mandi_id"].nunique()),
            "rows": int(len(feat)),
            "features": FEATURES,
            "spike_definition": f"max price in next 14 days > {s.spike_threshold_pct:.0f}% above today",
            "vs_naive_pinball_pct": {
                h: round(100 * (1 - lgb_m["by_horizon"][h]["pinball"] / naive_m["by_horizon"][h]["pinball"]), 1)
                for h in lgb_m["by_horizon"]
            },
        }
    )
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    backtest = Path(model_dir) / "backtest.json"
    backtest.write_text(json.dumps(report, indent=2, default=str))
    # every evaluation is recorded: MLflow (params, pooled metrics, artifacts) + DB (model_runs / eval_results)
    report["mlflow_run_id"] = log_mlflow(run, purpose="v1_train", artifacts={"backtest": str(backtest)})
    record(db, run, purpose="v1_train", mlflow_run_id=report["mlflow_run_id"])
    backtest.write_text(json.dumps(report, indent=2, default=str))
    return report


def _provenance(db: Session, feat: pd.DataFrame, synthetic: bool):
    """synthetic -> 'synthetic'; real -> per mandi 'real' / 'real_partial' from the readiness monitor."""
    if synthetic:
        return SYNTHETIC
    ready = mandi_price_ready(db)
    return {int(m): provenance_for(False, ready.get(int(m), False)) for m in feat["mandi_id"].unique()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="allow training on synthetic rows when no real data exists")
    ap.add_argument("--folds", type=int, default=8)
    args = ap.parse_args()
    with SessionLocal() as db:
        r = train(db, allow_synthetic=args.synthetic, n_folds=args.folds)
    tag = f"  [{r['provenance_label']}]"
    print(f"model {r['model_version']}{tag}  data {r['data_range']}  mandis {r['mandis']}")
    for model, m in r["metrics"].items():
        row = "  ".join(f"{h}: pin {v['pinball']:.0f} cov {v['coverage_p10_p90_pct']:.0f}%" for h, v in m["by_horizon"].items())
        print(f"  {model:18s} {row}  spike recall {m['spike']['recall']}")


if __name__ == "__main__":
    main()
