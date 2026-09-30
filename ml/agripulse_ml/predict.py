"""Write the latest 1-4 week forecasts for every mandi with recent prices.

    python -m agripulse_ml.predict

Output rows (forecasts table): p10/p50/p90 in Rs/quintal + spike probability.
The spike probability is for the next 14 days and is repeated on each horizon row.
"""
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.db import SessionLocal
from agripulse_api.models import Forecast
from agripulse_api.readiness import mandi_price_ready, provenance_for

from .data import load_arrivals, load_prices, load_weather
from .features import HORIZONS, QUANTILES, build_features
from .models import LoadedLightGBM

MAX_STALENESS_DAYS = 7


def predict_latest(db: Session, model_dir: str | None = None) -> dict:
    s = get_settings()
    d = Path(model_dir or s.model_dir)
    meta_path = d / "backtest.json"
    if not meta_path.exists():
        raise RuntimeError("No trained model. Run `python -m agripulse_ml.train` first.")
    meta = json.loads(meta_path.read_text())
    synthetic = bool(meta.get("trained_on_synthetic"))
    ready = {} if synthetic else mandi_price_ready(db)
    model = LoadedLightGBM(d)

    prices = load_prices(db, synthetic=synthetic)
    if prices.empty:
        return {"written": 0, "reason": "no prices"}
    feat = build_features(
        prices,
        load_weather(db, synthetic=synthetic),
        load_arrivals(db, synthetic=synthetic),
        spike_threshold_pct=s.spike_threshold_pct,
        with_targets=False,
    )
    # one issue row per mandi: its most recent *observed* price day
    feat = feat[feat["observed"]]
    latest = feat.sort_values("date").groupby("mandi_id").tail(1)
    newest = latest["date"].max()
    latest = latest[latest["date"] >= newest - timedelta(days=MAX_STALENESS_DAYS)]
    preds = model.predict(latest)

    # Pre-V3 B-1: calibrate the displayed range from this model's own track record (same model, rule 19)
    from .calibration import calibrate_range, calibration_config, serving_offsets
    from .features.config import lag

    cal_on = bool(calibration_config()["calibration"]["apply_to_display"])
    offsets = serving_offsets(db, "lightgbm_quantile", synthetic, newest, lag("prices")) if cal_on else {}

    written = 0
    for i, row in enumerate(latest.itertuples()):
        issue = row.date.date()
        for h in HORIZONS:
            vals = {f"p{int(q * 100)}": float(row.price * np.exp(preds[(h, f'p{int(q * 100)}')][i])) for q in QUANTILES}
            f = db.scalar(
                select(Forecast).where(
                    Forecast.mandi_id == row.mandi_id,
                    Forecast.commodity == "Tomato",
                    Forecast.issue_date == issue,
                    Forecast.horizon_weeks == h,
                    Forecast.model_name == "lightgbm_quantile",
                )
            )
            if f is None:
                f = Forecast(mandi_id=int(row.mandi_id), commodity="Tomato", issue_date=issue, horizon_weeks=h,
                             model_name="lightgbm_quantile")
                db.add(f)
            f.target_date = issue + timedelta(weeks=h)
            f.p10_raw, f.p50, f.p90_raw = round(vals["p10"], 0), round(vals["p50"], 0), round(vals["p90"], 0)
            if cal_on:
                lo, hi = calibrate_range(f.p10_raw, f.p50, f.p90_raw, offsets[h])
                f.calibration = offsets[h]["calibration"]
            else:
                lo, hi, f.calibration = f.p10_raw, f.p90_raw, "none"
            f.p10, f.p90 = round(lo, 0), round(hi, 0)
            f.spike_prob = round(float(preds["spike_prob"][i]), 3)
            f.model_version = meta.get("model_version", "")
            f.trained_on_synthetic = synthetic
            f.data_provenance = provenance_for(synthetic, ready.get(int(row.mandi_id), False))
            written += 1
    db.commit()
    return {"written": written, "mandis": int(len(latest)), "issue_date": str(newest.date()), "synthetic": synthetic,
            "calibration": {h: {k: v for k, v in o.items() if k != "off_lo" and k != "off_hi"} for h, o in offsets.items()}}


def run_job(db: Session) -> dict:
    from ingest.runs import tracked_run

    with tracked_run(db, "forecast") as run:
        out = predict_latest(db)
        run.rows, run.details = out["written"], out
    from agripulse_api.modelcfg import models_config

    if models_config()["tft"].get("write_forecasts"):  # V2-2, off by default; never replaces the display model
        from .tft.forecast import write_forecasts

        with tracked_run(db, "forecast_tft") as run:
            tft = write_forecasts(db)
            run.rows, run.details = tft["written"], tft
        out["tft"] = tft
    return out


def main() -> None:
    with SessionLocal() as db:
        print(run_job(db))


if __name__ == "__main__":
    main()
