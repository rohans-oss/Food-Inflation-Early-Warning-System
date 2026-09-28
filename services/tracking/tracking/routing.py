"""Road routing via self-hosted OSRM, with an honest straight-line fallback.

OSRM route service: GET {OSRM_URL}/route/v1/driving/{lon},{lat};{lon},{lat}
    ?overview=full&geometries=geojson
  -> {"code": "Ok", "routes": [{"distance": m, "duration": s, "geometry": {"coordinates": [[lon, lat], ...]}}]}

Without OSRM (OSRM_URL empty or unreachable) the route is the straight line,
distance x FALLBACK_ROAD_FACTOR, at FALLBACK_SPEED_KMPH, and `source` says "haversine"
so the UI can say "approximate".
"""
from dataclasses import dataclass

import httpx

from agripulse_api.config import get_settings

from .geo import haversine_km


@dataclass
class Route:
    distance_km: float
    duration_min: float
    geometry: list[list[float]]  # [[lon, lat], ...]
    source: str  # osrm|haversine


def straight_line(o_lat: float, o_lon: float, d_lat: float, d_lon: float, steps: int = 20) -> Route:
    s = get_settings()
    km = haversine_km(o_lat, o_lon, d_lat, d_lon) * s.fallback_road_factor
    geom = [[o_lon + (d_lon - o_lon) * i / steps, o_lat + (d_lat - o_lat) * i / steps] for i in range(steps + 1)]
    return Route(round(km, 2), round(km / s.fallback_speed_kmph * 60, 1), geom, "haversine")


def route(o_lat: float, o_lon: float, d_lat: float, d_lon: float, client: httpx.Client | None = None) -> Route:
    s = get_settings()
    if not s.osrm_url:
        return straight_line(o_lat, o_lon, d_lat, d_lon)
    url = f"{s.osrm_url.rstrip('/')}/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}"
    try:
        c = client or httpx.Client()
        try:
            r = c.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=10)
        finally:
            if client is None:
                c.close()
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(data.get("code"))
        best = data["routes"][0]
        return Route(
            round(best["distance"] / 1000, 2),
            round(best["duration"] / 60, 1),
            best["geometry"]["coordinates"],
            "osrm",
        )
    except (httpx.HTTPError, ValueError, KeyError):
        return straight_line(o_lat, o_lon, d_lat, d_lon)


def road_km(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> tuple[float, float, str]:
    """(km, minutes, source) — used by the recommender for many mandis at once."""
    r = route(o_lat, o_lon, d_lat, d_lon)
    return r.distance_km, r.duration_min, r.source
