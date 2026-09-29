"""Write TFT forecasts to the forecasts table, behind `[tft] write_forecasts` in config/models.toml (V2-2).

    python -m agripulse_ml.tft.forecast            # no-op unless write_forecasts = true
    python -m agripulse_ml.tft.forecast --force    # ignore the flag (for a one-off look)

Rows are written under model_name = "tft" (the conformal-calibrated variant) with their own data_provenance.
Users keep seeing the [display] model: every read path filters by it, so TFT rows never mix in silently.
Data source follows `[tft] data_provenance`: "synthetic" reads the synthetic DB rows (source = 'synthetic'),
"real" reads real rows, and the readiness monitor stamps each mandi real / real_partial.
"""
import argparse
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.modelcfg import models_config
from agripulse_api.models import Forecast

from ..features.inputs import Inputs
from ..features.store import build_table
from .model import HORIZONS, TFTForecaster

MODEL_NAME = "tft"
MAX_STALENESS_DAYS = 7


def write_forecasts(db: Session, cfg: dict | None = None, force: bool = False, as_of=None) -> dict:
    cfg = dict(cfg or models_config()["tft"])
    if not (cfg.get("write_forecasts") or force):
        return {"written": 0, "reason": "[tft] write_forecasts = false"}
    synthetic = cfg["data_provenance"] == "synthetic"
    table = build_table(Inputs.from_db(db, synthetic=synthetic), cfg["feature_set"], as_of=as_of)
    df = table.df
    if df.empty:
        return {"written": 0, "reason": "no prices"}
    cutoff = df["date"].max() + pd.Timedelta(days=1)
    lag = pd.Timedelta(days=table.label_lag_days)
    # rows whose 4-week label is already published: only these feed the conformal calibration
    labelled = df[df["target_date_h4"] + lag < cutoff]
    model = TFTForecaster(cfg, table.columns, calibrate=True, label_lag_days=table.label_lag_days)
    model.fit(labelled, history=df)

    latest = df[df["observed"]].sort_values("date").groupby("mandi_id").tail(1)
    latest = latest[latest["date"] >= latest["date"].max() - timedelta(days=MAX_STALENESS_DAYS)].reset_index(drop=True)
    p = model.predict(latest, context=df)
    version = f"tft-{(cutoff - pd.Timedelta(days=1)).date()}"
    written = 0
    for i, row in enumerate(latest.itertuples()):
        issue = row.date.date()
        prov = table.mandi_provenance[int(row.mandi_id)]
        for h in HORIZONS:
            f = db.scalar(select(Forecast).where(
                Forecast.mandi_id == int(row.mandi_id), Forecast.commodity == "Tomato", Forecast.issue_date == issue,
                Forecast.horizon_weeks == h, Forecast.model_name == MODEL_NAME))
            if f is None:
                f = Forecast(mandi_id=int(row.mandi_id), commodity="Tomato", issue_date=issue, horizon_weeks=h,
                             model_name=MODEL_NAME)
                db.add(f)
            f.target_date = issue + timedelta(weeks=h)
            f.p10, f.p50, f.p90 = (round(float(row.price * np.exp(p[(h, q)][i])), 0) for q in ("p10", "p50", "p90"))
            f.spike_prob = round(float(p["spike_prob"][i]), 3)
            f.model_version = version
            f.trained_on_synthetic = synthetic
            f.data_provenance = prov
            written += 1
    db.commit()
    return {"written": written, "mandis": int(len(latest)), "model_name": MODEL_NAME, "model_version": version,
            "data_provenance": table.data_provenance, "epochs": model.state["epochs"],
            "fit_seconds": model.state["fit_seconds"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="write even if [tft] write_forecasts = false")
    args = ap.parse_args()
    from agripulse_api.db import SessionLocal

    with SessionLocal() as db:
        print(write_forecasts(db, force=args.force))


if __name__ == "__main__":
    main()
