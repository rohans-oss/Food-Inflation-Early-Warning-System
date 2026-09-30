"""Sentinel-2 L2A scene search on Element 84 Earth Search (STAC API, no key).

Verified against real responses (docs/data-sources.md, 2026-09-29):
  GET {url}/search?collections=sentinel-2-l2a&bbox=W,S,E,N&datetime=A/B&limit=N
  -> FeatureCollection; features[].properties: datetime, eo:cloud_cover, s2:processing_baseline, proj:epsg, grid:code
     features[].assets.{red,nir,scl}.href (COG on sentinel-cogs S3) + raster:bands[0].{scale, offset}
  Pagination: links[] with rel = "next" (a GET href).
Scale and offset are read PER ITEM: processing baseline >= 04.00 (2022+) has offset -0.1, older scenes 0.

Found on the first real pilot run (2026-09-29):
  * some items lack the red / nir / scl COG assets -> they are skipped and reported, never guessed at;
  * old scenes appear twice, e.g. S2B_43PHQ_20180213_0_L2A (baseline 00.01) and ..._1_L2A (baseline 05.00, the
    reprocessed version) with the same tile and time -> only the highest version number is kept.

Found on the full pilot (2026-09-30), VERIFIED ON PIXELS: items with `earthsearch:boa_offset_applied: true` still
list `raster:bands` offset -0.1, but their COG values have ALREADY had the +1000 removed. Same place, same day,
S2B_43PHQ_20191105_0_L2A (baseline 02.13, flag false) vs _1_L2A (05.00, flag true): red DN median 768 vs 773,
NIR 3098 vs 3149 (a +1000 would put every value above 1000). Applying -0.1 again drove red reflectance negative and
NDVI above 1. Rule: when the flag is true the offset is 0; otherwise use raster:bands.
"""
import re
from dataclasses import dataclass

import httpx


@dataclass
class Scene:
    id: str
    datetime: str
    date: str
    cloud: float | None
    epsg: int
    tile: str | None
    baseline: str | None
    boa_offset_applied: bool | None
    href: dict        # {"red": url, "nir": url, "scl": url}
    scale: dict       # per asset
    offset: dict      # per asset


def _band(asset: dict) -> tuple[float, float]:
    b = (asset.get("raster:bands") or [{}])[0]
    return float(b.get("scale", 1.0)), float(b.get("offset", 0.0))


REQUIRED = ("red", "nir", "scl")


class MissingAssets(ValueError):
    pass


def parse(feature: dict) -> Scene:
    p, a = feature["properties"], feature.get("assets", {})
    missing = [k for k in REQUIRED if k not in a or "href" not in a[k]]
    if missing:
        raise MissingAssets(f"{feature.get('id')}: no {missing} asset (has {sorted(a)[:8]}...)")
    epsg = p.get("proj:epsg")
    if epsg is None and str(p.get("proj:code", "")).startswith("EPSG:"):
        epsg = int(p["proj:code"].split(":")[1])
    applied = p.get("earthsearch:boa_offset_applied")
    sc, off = {}, {}
    for k in ("red", "nir", "scl"):
        sc[k], off[k] = _band(a[k])
        if applied is True:
            off[k] = 0.0  # the provider already removed BOA_ADD_OFFSET from the pixels (see module docstring)
    return Scene(id=feature["id"], datetime=p["datetime"], date=p["datetime"][:10], cloud=p.get("eo:cloud_cover"),
                 epsg=int(epsg), tile=p.get("grid:code"), baseline=p.get("s2:processing_baseline"),
                 boa_offset_applied=applied, href={k: a[k]["href"] for k in ("red", "nir", "scl")}, scale=sc, offset=off)


def _version(scene_id: str) -> int:
    m = re.search(r"_(\d+)_L2A$", scene_id)
    return int(m.group(1)) if m else 0


def dedupe(scenes: list[Scene]) -> list[Scene]:
    """Same tile + same acquisition time = the same image; keep the highest reprocessing version."""
    best: dict = {}
    for sc in scenes:
        key = (sc.tile, sc.datetime[:16])
        if key not in best or (_version(sc.id), sc.baseline or "") > (_version(best[key].id), best[key].baseline or ""):
            best[key] = sc
    return list(best.values())


def search(bbox, start: str, end: str, cfg: dict, client: httpx.Client | None = None, max_pages: int = 500,
           log=None, skipped: list | None = None) -> list[Scene]:
    """All scenes intersecting bbox (lon/lat) in [start, end], cloud <= max_cloud_pct, one per tile + time, oldest
    first. Items without red / nir / scl assets go to `skipped` (and the log) instead of failing the search."""
    s = cfg["stac"]
    url = f"{s['url'].rstrip('/')}/search"
    params = {"collections": s["collection"], "bbox": ",".join(f"{v:.5f}" for v in bbox),
              "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": int(s["page_limit"])}
    own = client is None
    c = client or httpx.Client(timeout=60)
    out, seen = [], set()
    try:
        for _ in range(max_pages):
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            for f in data.get("features", []):
                try:
                    sc = parse(f)
                except MissingAssets as e:
                    if skipped is not None:
                        skipped.append(str(e))
                    continue
                if sc.id in seen or (sc.cloud is not None and sc.cloud > float(s["max_cloud_pct"])):
                    continue
                seen.add(sc.id)
                out.append(sc)
            nxt = next((ln for ln in data.get("links", []) if ln.get("rel") == "next"), None)
            if not nxt or not data.get("features"):
                break
            url, params = nxt["href"], None
    finally:
        if own:
            c.close()
    n = len(out)
    out = dedupe(out)
    if log:
        if skipped:
            log(f"  skipped {len(skipped)} item(s) without red/nir/scl assets, e.g. {skipped[0]}")
        if n != len(out):
            log(f"  dropped {n - len(out)} duplicate(s): older processing of the same tile and time")
    return sorted(out, key=lambda x: (x.datetime, x.id))
