"""Load a pilot observations CSV (from satellite.run, possibly produced on another machine) into satellite_obs.

    python -m agripulse_ml.satellite.load data/satellite/observations.csv

Idempotent: rows are upserted on (scene_id, district).
"""
import argparse
import sys
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.models import SatelliteObs

from .run import BASE_FIELDS as FIELDS

NUM = ["scene_cloud_pct", "clear_frac", "ndvi_median", "ndvi_mean", "ndvi_p25", "ndvi_p75"]
INT = ["cropland_px", "in_scene_px", "clear_px"]


def load_csv(db: Session, path: str) -> dict:
    df = pd.read_csv(path, dtype={"scene_id": str, "district": str, "tile": str, "baseline": str})
    missing = set(FIELDS) - set(df.columns)
    if missing:
        raise ValueError(f"{path} is not a satellite.run observations file (missing {sorted(missing)})")
    have = {(s, d): o for o in db.scalars(select(SatelliteObs)) for s, d in [(o.scene_id, o.district)]}
    added = updated = 0
    for r in df.to_dict("records"):
        key = (r["scene_id"], r["district"])
        o = have.get(key)
        if o is None:
            o = SatelliteObs(scene_id=r["scene_id"], district=r["district"])
            db.add(o)
            have[key] = o
            added += 1
        else:
            updated += 1
        o.date = date.fromisoformat(str(r["date"])[:10])
        o.tile = None if pd.isna(r["tile"]) else r["tile"]
        o.baseline = None if pd.isna(r["baseline"]) else str(r["baseline"])
        for k in NUM:
            setattr(o, k, None if pd.isna(r[k]) else float(r[k]))
        for k in INT:
            setattr(o, k, int(r[k]))
    db.commit()
    return {"rows": len(df), "added": added, "updated": updated,
            "with_ndvi": int(df["ndvi_median"].notna().sum()), "districts": sorted(df["district"].unique().tolist())}


def run_job(db: Session, path: str) -> dict:
    from ingest.runs import tracked_run

    with tracked_run(db, "satellite_load") as run:
        out = load_csv(db, path)
        run.rows, run.details = out["rows"], out
        return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    a = ap.parse_args(argv)
    from agripulse_api.db import SessionLocal

    with SessionLocal() as db:
        print(run_job(db, a.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
