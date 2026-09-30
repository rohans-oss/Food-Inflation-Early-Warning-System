"""V2-4 pilot fetch: Sentinel-2 NDVI over cropland per district -> observations CSV (REAL data).

    python -m agripulse_ml.satellite.run --out data/satellite --dry-run      # count scenes, estimate data + time
    python -m agripulse_ml.satellite.run --out data/satellite                # fetch (resumable: re-run to continue)
    python -m agripulse_ml.satellite.load data/satellite/observations.csv   # into the database (satellite_obs)

Needs the `satellite` extra (`pip install -e .[satellite]`) and network access to earth-search.aws.element84.com,
sentinel-cogs.s3.us-west-2.amazonaws.com and esa-worldcover.s3.eu-central-1.amazonaws.com.
One row per (scene, district). A row with an empty ndvi_median means the district was too cloudy in that scene;
it is kept so the run can resume and so cloud gaps are visible.
"""
import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

from .config import satellite_config
from .stac import search

BASE_FIELDS = ["district", "scene_id", "date", "tile", "baseline", "scene_cloud_pct", "cropland_px", "in_scene_px",
               "clear_px", "clear_frac", "ndvi_median", "ndvi_mean", "ndvi_p25", "ndvi_p75"]
FIELDS = BASE_FIELDS + ["offset_red", "pipeline_version"]
# Rough cost of one scene x district at 80 m: three overview windows. Measured on the first real scenes you run;
# until then this is an ESTIMATE used only for the dry-run warning.
EST_MB_PER_SCENE = 3.0
EST_SECONDS_PER_SCENE = 4.0


def bbox_of(d: dict) -> tuple[float, float, float, float]:
    import math

    dlat = float(d["radius_km"]) / 111.32
    dlon = float(d["radius_km"]) / (111.32 * math.cos(math.radians(float(d["lat"]))))
    return (float(d["lon"]) - dlon, float(d["lat"]) - dlat, float(d["lon"]) + dlon, float(d["lat"]) + dlat)


def migrate_v1(path: Path, log=print) -> dict | None:
    """A version-1 file (no pipeline_version column) applied the -0.1 offset to scenes whose pixels already had it
    removed. Keep only rows from processing baselines < 04.00 (never offset, verified correct); the rest are
    re-fetched. The original file is kept as observations.v1.csv."""
    if not path.exists():
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
        header = rows[0].keys() if rows else []
    if "pipeline_version" in header:
        return None
    backup = path.with_name("observations.v1.csv")
    path.replace(backup)
    keep = [r for r in rows if r.get("baseline") and r["baseline"] < "04.00"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in keep:
            w.writerow({**{k: r.get(k) for k in BASE_FIELDS}, "offset_red": 0.0, "pipeline_version": 2})
    log(f"migrated {path.name}: kept {len(keep)} rows from baselines < 04.00, {len(rows) - len(keep)} will be "
        f"re-fetched with the corrected offset (original saved as {backup.name})")
    return {"kept": len(keep), "redo": len(rows) - len(keep)}


def done_keys(path: Path) -> set:
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {(r["scene_id"], r["district"]) for r in csv.DictReader(f)}


def run(out: Path, start: str, end: str, districts: list[dict], cfg: dict, dry_run: bool = False,
        client=None, limit: int | None = None, log=print) -> dict:
    from .extract import district_stats

    out.mkdir(parents=True, exist_ok=True)
    obs = out / "observations.csv"
    if not dry_run:
        migrate_v1(obs, log)
    done = done_keys(obs)
    plan = []
    for d in districts:
        skipped: list = []
        scenes = search(bbox_of(d), start, end, cfg, client=client, log=log, skipped=skipped)
        todo = [s for s in scenes if (s.id, d["name"]) not in done]
        plan.append((d, scenes, todo))
        log(f"{d['name']}: {len(scenes)} scenes with cloud <= {cfg['stac']['max_cloud_pct']}%, {len(todo)} still to fetch")
    n = sum(len(t) for _, _, t in plan)
    est = {"scenes_to_fetch": n, "est_gb": round(n * EST_MB_PER_SCENE / 1024, 1),
           "est_hours": round(n * EST_SECONDS_PER_SCENE / 3600, 1)}
    log(f"TOTAL {n} scene x district reads, roughly {est['est_gb']} GB and {est['est_hours']} h (estimate)")
    if dry_run:
        return est
    had_file = obs.exists()
    written, failed, t0 = 0, 0, time.time()
    with open(obs, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not had_file:
            w.writeheader()
        for d, _, todo in plan:
            for s in todo[:limit] if limit else todo:
                try:
                    row = district_stats(s, d, cfg)
                except Exception as e:  # a bad scene must not stop a multi-hour run; it is retried next time
                    failed += 1
                    log(f"  skip {s.id} {d['name']}: {type(e).__name__}: {e}")
                    continue
                w.writerow({k: row.get(k) for k in FIELDS})
                f.flush()
                written += 1
                if written % 25 == 0:
                    rate = (time.time() - t0) / written
                    log(f"  {written}/{n} done, {rate:.1f} s/scene, ~{(n - written) * rate / 60:.0f} min left")
    return {**est, "written": written, "failed": failed, "seconds": round(time.time() - t0, 1), "file": str(obs)}


def main(argv=None):
    cfg = satellite_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default=cfg["pilot"]["start"])
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--districts", default="", help="comma-separated names from [pilot] (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="at most N scenes per district (smoke test)")
    a = ap.parse_args(argv)
    names = [x.strip() for x in a.districts.split(",") if x.strip()]
    ds = [d for d in cfg["pilot"]["districts"] if not names or d["name"] in names]
    if not ds:
        print("no matching districts in config/satellite.toml [pilot]", file=sys.stderr)
        return 2
    print(run(Path(a.out), a.start, a.end, ds, cfg, dry_run=a.dry_run, limit=a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
