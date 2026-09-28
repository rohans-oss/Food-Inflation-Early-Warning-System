import math

EARTH_R_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))


def point_segment_km(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Distance (km) from point to segment a-b (lon, lat pairs) and the fraction t along it.
    Equirectangular projection around the point: fine at segment scale."""
    k = math.cos(math.radians(lat))
    ax, ay = (a[0] - lon) * k, a[1] - lat
    bx, by = (b[0] - lon) * k, b[1] - lat
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py) * 111.195, t


def remaining_along_route_km(lat: float, lon: float, route: list[list[float]]) -> tuple[float, float]:
    """(remaining km along the polyline from the closest point, off-route distance km)."""
    if not route or len(route) < 2:
        return 0.0, 0.0
    best = (float("inf"), 0, 0.0)
    for i in range(len(route) - 1):
        d, t = point_segment_km(lat, lon, route[i], route[i + 1])
        if d < best[0]:
            best = (d, i, t)
    off, i, t = best
    seg = haversine_km(route[i][1], route[i][0], route[i + 1][1], route[i + 1][0])
    rem = seg * (1 - t)
    for j in range(i + 1, len(route) - 1):
        rem += haversine_km(route[j][1], route[j][0], route[j + 1][1], route[j + 1][0])
    return rem, off
