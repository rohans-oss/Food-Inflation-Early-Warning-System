"""Per-scene, per-district NDVI over cropland (REAL data).

For one Sentinel-2 scene and one district (approximated as a circle around its HQ):
  1. a grid in the scene's UTM CRS at `resolution_m`, covering the circle
  2. red, nir read from the COG OVERVIEW closest to that resolution (average resampling), scl (nearest)
  3. cropland mask from ESA WorldCover (class 40, mode resampling onto the same grid)
  4. keep pixels: inside the circle, cropland, SCL clear land (config scl_valid), red and nir > 0 (0 = no data)
  5. reflectance = DN * scale + offset (per scene: baseline >= 04.00 has offset -0.1), NDVI = (nir - red) / (nir + red)
Returns median / mean / quartiles and how many cropland pixels were clear (cloud cover inside the district).
"""
import math
import os
from functools import lru_cache

import numpy as np

from .stac import Scene

GDAL_ENV = {  # anonymous HTTP range reads of public COGs
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


def _grid(epsg: int, lat: float, lon: float, radius_km: float, res: float):
    from pyproj import Transformer
    from rasterio.transform import from_origin

    x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    r = radius_km * 1000
    minx, maxy = math.floor((x - r) / res) * res, math.ceil((y + r) / res) * res
    w = int(math.ceil((x + r - minx) / res))
    h = int(math.ceil((maxy - (y - r)) / res))
    transform = from_origin(minx, maxy, res, res)
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    px, py = minx + (cols + 0.5) * res, maxy - (rows + 0.5) * res
    circle = (px - x) ** 2 + (py - y) ** 2 <= r * r
    return transform, (h, w), circle


def _open_best(href: str, target_res_m: float, degrees: bool = False):
    """Open the COG overview whose resolution is closest to (not coarser than) the target."""
    import rasterio

    with rasterio.open(href) as src:
        native = abs(src.res[0]) * (111_320 if degrees else 1)
        factors = src.overviews(1)
    level = None
    for i, f in enumerate(factors):
        if native * f <= target_res_m:
            level = i
    return rasterio.open(href, overview_level=level) if level is not None else rasterio.open(href)


def _warp(href: str, transform, shape, epsg: int, res: float, resampling, degrees: bool = False,
          dst: np.ndarray | None = None) -> np.ndarray:
    import rasterio
    from rasterio.warp import reproject

    out = dst if dst is not None else np.zeros(shape, dtype=np.float32)
    with _open_best(href, res, degrees) as src:
        reproject(source=rasterio.band(src, 1), destination=out, src_nodata=0, dst_nodata=0,
                  dst_transform=transform, dst_crs=f"EPSG:{epsg}", resampling=resampling, init_dest_nodata=dst is None)
    return out


def worldcover_tiles(lat: float, lon: float, radius_km: float) -> list[str]:
    """3x3-degree tile names (e.g. N12E078) touched by the circle."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * math.cos(math.radians(lat)))
    names = set()
    for la in (lat - dlat, lat + dlat):
        for lo in (lon - dlon, lon + dlon):
            b_la, b_lo = math.floor(la / 3) * 3, math.floor(lo / 3) * 3
            names.add(f"{'N' if b_la >= 0 else 'S'}{abs(b_la):02d}{'E' if b_lo >= 0 else 'W'}{abs(b_lo):03d}")
    return sorted(names)


@lru_cache(maxsize=64)
def cropland_mask(epsg: int, lat: float, lon: float, radius_km: float, res: float, url_template: str, crop_class: int):
    from rasterio.enums import Resampling

    transform, shape, circle = _grid(epsg, lat, lon, radius_km, res)
    lc = np.zeros(shape, dtype=np.float32)
    for tile in worldcover_tiles(lat, lon, radius_km):
        _warp(url_template.format(tile=tile), transform, shape, epsg, res, Resampling.mode, degrees=True, dst=lc)
    return (lc == crop_class) & circle


def district_stats(scene: Scene, district: dict, cfg: dict) -> dict:
    import rasterio
    from rasterio.enums import Resampling

    ex, cl = cfg["extract"], cfg["cropland"]
    res = float(ex["resolution_m"])
    lat, lon, rad = float(district["lat"]), float(district["lon"]), float(district["radius_km"])
    transform, shape, circle = _grid(scene.epsg, lat, lon, rad, res)
    with rasterio.Env(**{k: os.environ.get(k, v) for k, v in GDAL_ENV.items()}):
        crop = cropland_mask(scene.epsg, lat, lon, rad, res, cl["url_template"], int(cl["class"]))
        red = _warp(scene.href["red"], transform, shape, scene.epsg, res, Resampling.average)
        nir = _warp(scene.href["nir"], transform, shape, scene.epsg, res, Resampling.average)
        scl = _warp(scene.href["scl"], transform, shape, scene.epsg, res, Resampling.nearest)
    in_scene = crop & (red > 0) & (nir > 0) & (scl > 0)
    clear = in_scene & np.isin(scl.astype(int), [int(v) for v in ex["scl_valid"]])
    r = red * scene.scale["red"] + scene.offset["red"]
    n = nir * scene.scale["nir"] + scene.offset["nir"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (n - r) / (n + r)
    v = ndvi[clear & np.isfinite(ndvi) & ((n + r) > 0)]
    base = {"district": district["name"], "scene_id": scene.id, "date": scene.date, "tile": scene.tile,
            "baseline": scene.baseline, "scene_cloud_pct": scene.cloud, "cropland_px": int(crop.sum()),
            "in_scene_px": int(in_scene.sum()), "clear_px": int(v.size),
            "clear_frac": round(float(v.size / in_scene.sum()), 4) if in_scene.sum() else 0.0}
    if v.size < int(ex["min_valid_pixels"]):
        return {**base, "ndvi_median": None, "ndvi_mean": None, "ndvi_p25": None, "ndvi_p75": None}
    q25, q50, q75 = np.percentile(v, [25, 50, 75])
    return {**base, "ndvi_median": round(float(q50), 4), "ndvi_mean": round(float(v.mean()), 4),
            "ndvi_p25": round(float(q25), 4), "ndvi_p75": round(float(q75), 4)}
