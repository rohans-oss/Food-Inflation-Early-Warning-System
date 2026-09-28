"""Train, backtest and persist the tomato forecaster.

    python -m agripulse_ml.train                 # real data only
    python -m agripulse_ml.train --synthetic     # allow source='synthetic' rows (dev/demo)

Writes to MODEL_DIR (default ml/artifacts):
    lgb_h{1..4}_p{10,50,90}.txt, lgb_spike.txt, model_meta.json   final models (all data)
    backtest.json                                                  walk-forward report
Logs to MLflow too when `pip install -e .[mlflow]` and MLFLOW_TRACKING_URI are set.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.db import SessionLocal

from .data import load_arrivals, load_prices, load_weather
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
    report = walk_forward(feat, BacktestConfig(n_folds=n_folds, alert_probability=s.spike_alert_probability))
    final = LightGBMQuantileForecaster().fit(feat)
    final.save(model_dir)
    version = datetime.now(timezone.utc).strftime(MODEL_VERSION_FMT)
    lgb_m, naive_m = report["metrics"]["lightgbm_quantile"], report["metrics"]["naive"]
    report.update(
        {
            "model_version": version,
            "trained_on_synthetic": synthetic,
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
    (Path(model_dir) / "backtest.json").write_text(json.dumps(report, indent=2, default=str))
    _maybe_mlflow(report, model_dir)
    return report


def _maybe_mlflow(report: dict, model_dir: str) -> None:
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        return
    try:
        import mlflow
    except ImportError:
        return
    with mlflow.start_run(run_name=f"lgb-quantile-{report['model_version']}"):
        mlflow.log_params({"trained_on_synthetic": report["trained_on_synthetic"], "rows": report["rows"]})
        for model, m in report["metrics"].items():
            for h, v in m["by_horizon"].items():
                mlflow.log_metric(f"{model}_{h}_pinball", v["pinball"])
                mlflow.log_metric(f"{model}_{h}_coverage", v["coverage_p10_p90_pct"])
        mlflow.log_artifacts(model_dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="allow training on synthetic rows when no real data exists")
    ap.add_argument("--folds", type=int, default=8)
    args = ap.parse_args()
    with SessionLocal() as db:
        r = train(db, allow_synthetic=args.synthetic, n_folds=args.folds)
    tag = " [SYNTHETIC DATA]" if r["trained_on_synthetic"] else ""
    print(f"model {r['model_version']}{tag}  data {r['data_range']}  mandis {r['mandis']}")
    for model, m in r["metrics"].items():
        row = "  ".join(f"{h}: pin {v['pinball']:.0f} cov {v['coverage_p10_p90_pct']:.0f}%" for h, v in m["by_horizon"].items())
        print(f"  {model:18s} {row}  spike recall {m['spike']['recall']}")


if __name__ == "__main__":
    main()
