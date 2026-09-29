"""Sentinel-2 L2A scene search on Element 84 Earth Search (STAC API, no key).

Verified against real responses (docs/data-sources.md, 2026-09-29):
  GET {url}/search?collections=sentinel-2-l2a&bbox=W,S,E,N&datetime=A/B&limit=N
  -> FeatureCollection; features[].properties: datetime, eo:cloud_cover, s2:processing_baseline, proj:epsg, grid:code
     features[].assets.{red,nir,scl}.href (COG on sentinel-cogs S3) + raster:bands[0].{scale, offset}
  Pagination: links[] with rel = "next" (a GET href).
Scale and offset are read PER ITEM: processing baseline >= 04.00 (2022+) has offset -0.1, older scenes 0.
"""
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
    href: dict        # {"red": url, "nir": url, "scl": url}
    scale: dict       # per asset
    offset: dict      # per asset


def _band(asset: dict) -> tuple[float, float]:
    b = (asset.get("raster:bands") or [{}])[0]
    return float(b.get("scale", 1.0)), float(b.get("offset", 0.0))


def parse(feature: dict) -> Scene:
    p, a = feature["properties"], feature["assets"]
    epsg = p.get("proj:epsg")
    if epsg is None and str(p.get("proj:code", "")).startswith("EPSG:"):
        epsg = int(p["proj:code"].split(":")[1])
    sc, off = {}, {}
    for k in ("red", "nir", "scl"):
        sc[k], off[k] = _band(a[k])
    return Scene(id=feature["id"], datetime=p["datetime"], date=p["datetime"][:10], cloud=p.get("eo:cloud_cover"),
                 epsg=int(epsg), tile=p.get("grid:code"), baseline=p.get("s2:processing_baseline"),
                 href={k: a[k]["href"] for k in ("red", "nir", "scl")}, scale=sc, offset=off)


def search(bbox, start: str, end: str, cfg: dict, client: httpx.Client | None = None, max_pages: int = 500) -> list[Scene]:
    """All scenes intersecting bbox (lon/lat) in [start, end], cloud <= max_cloud_pct, oldest first."""
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
                sc = parse(f)
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
    return sorted(out, key=lambda x: (x.datetime, x.id))
