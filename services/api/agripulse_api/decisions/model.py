"""Problem definition and the cost model shared by the rule, the optimizer and the evaluator."""
from dataclasses import dataclass, field
from typing import Callable

from ..supply import cost_config, spoilage_pct

# (o_lat, o_lon, d_lat, d_lon) -> (road km, minutes, source "osrm"|"haversine")
DistanceFn = Callable[[float, float, float, float], tuple[float, float, str]]


@dataclass(frozen=True)
class Lot:
    id: int | str
    lat: float
    lon: float
    tons: float
    crop: str = "Tomato"


@dataclass(frozen=True)
class Vehicle:
    id: int | str
    capacity_tons: float
    lat: float  # base (where the truck starts and returns)
    lon: float


@dataclass(frozen=True)
class MandiOption:
    id: int | str
    name: str
    lat: float
    lon: float
    p10: float  # calibrated forecast, Rs/quintal (display model)
    p50: float
    p90: float
    temp_c: float
    typical_daily_tons: float | None = None  # None = unknown -> no absorption cap
    data_provenance: str = "synthetic"
    tons_already_coming: float = 0.0  # live only: tonnes on the road to this mandi now (V1 tracking) use up its room


@dataclass
class Problem:
    lots: list[Lot]
    vehicles: list[Vehicle]
    mandis: list[MandiOption]
    distance: DistanceFn
    cfg: dict = field(default_factory=cost_config)

    def __post_init__(self):
        self._km: dict = {}

    def leg(self, a_lat, a_lon, b_lat, b_lon) -> tuple[float, float, str]:
        k = (round(a_lat, 5), round(a_lon, 5), round(b_lat, 5), round(b_lon, 5))
        if k not in self._km:
            self._km[k] = self.distance(a_lat, a_lon, b_lat, b_lon)
        return self._km[k]

    # ---- cost model (one place; every method is scored with it) ----
    def rate_per_km(self, v: Vehicle) -> float:
        t = self.cfg["transport"]["vehicle"]
        return t["base_rate_per_km"] + t["rate_per_km_per_capacity_ton"] * v.capacity_tons

    def vehicle_km(self, v: Vehicle, lot: Lot, m: MandiOption) -> float:
        out = self.leg(v.lat, v.lon, lot.lat, lot.lon)[0] + self.leg(lot.lat, lot.lon, m.lat, m.lon)[0]
        if self.cfg["transport"]["vehicle"].get("count_return_leg", True):
            out += self.leg(m.lat, m.lon, v.lat, v.lon)[0]
        return out

    def trip_cost(self, v: Vehicle, lot: Lot, m: MandiOption) -> float:
        return self.vehicle_km(v, lot, m) * self.rate_per_km(v)

    def spoilage(self, lot: Lot, m: MandiOption) -> float:
        """% of value lost between pickup and mandi (V1 formula, config/recommender.toml [spoilage])."""
        minutes = self.leg(lot.lat, lot.lon, m.lat, m.lon)[1]
        return spoilage_pct(minutes / 60, m.temp_c, lot.crop)

    def net(self, lot: Lot, m: MandiOption, v: Vehicle, price: float) -> float:
        """Net value of one lot at `price` Rs/quintal: gross - spoilage - vehicle trip cost."""
        gross = price * lot.tons * 10
        return gross * (1 - self.spoilage(lot, m) / 100) - self.trip_cost(v, lot, m)

    def mandi_cap(self, m: MandiOption) -> float | None:
        share = self.cfg["constraints"]["mandi_extra_share"]
        return None if m.typical_daily_tons is None else share * m.typical_daily_tons - m.tons_already_coming

    @property
    def max_spoilage(self) -> float:
        return self.cfg["constraints"]["max_spoilage_pct"]

    @property
    def stop_minutes(self) -> float:
        return float(self.cfg.get("consolidation", {}).get("loading_minutes_per_stop", 0.0))

    def route(self, v: "Vehicle", trips: list[tuple["MandiOption", list[Lot]]]) -> dict:
        """One truck's day: base -> trip 1 pickups (in order) -> mandi 1 -> trip 2 pickups -> mandi 2 ... -> base.
        Returns km, minutes (incl. loading stops after the first pickup of each trip), empty km, and per lot the
        minutes from its pickup to its mandi (spoilage clock). For ONE lot it equals V3-0's vehicle_km / spoilage."""
        km = minutes = empty = 0.0
        clock = {}
        pos = (v.lat, v.lon)
        for m, lots in trips:
            first = True
            ride = []  # (lot, minutes since its pickup)
            for lot in lots:
                d, t, _ = self.leg(*pos, lot.lat, lot.lon)
                extra = 0.0 if first else self.stop_minutes
                km, minutes = km + d, minutes + t + extra
                if first:
                    empty += d
                ride = [(x, s + t + extra) for x, s in ride] + [(lot, 0.0)]
                pos, first = (lot.lat, lot.lon), False
            d, t, _ = self.leg(*pos, m.lat, m.lon)
            km, minutes = km + d, minutes + t
            for x, s in ride:
                clock[x.id] = s + t
            pos = (m.lat, m.lon)
        if self.cfg["transport"]["vehicle"].get("count_return_leg", True):
            d, t, _ = self.leg(*pos, v.lat, v.lon)
            km, minutes, empty = km + d, minutes + t, empty + d
        return {"km": km, "minutes": minutes, "empty_km": empty, "clock": clock}

    def spoilage_after(self, lot: Lot, m: "MandiOption", minutes: float) -> float:
        from ..supply import spoilage_pct as _sp

        return _sp(minutes / 60, m.temp_c, lot.crop)

    @property
    def max_driver_minutes(self) -> float:
        return 60.0 * float(self.cfg.get("consolidation", {}).get("max_driver_hours", 1e9))


@dataclass
class Assignment:
    lot_id: int | str
    mandi_id: int | str
    vehicle_id: int | str | None  # None = no vehicle could take it (the rule's failure mode)
    seq: int = 0   # V3-1: pickup order within the trip (shared loads)
    trip: int = 1  # V3-1: 1 = outbound; 2 = return load after the first delivery


@dataclass
class Plan:
    method: str  # "rule" | "optimizer" | "optimizer_p10"
    assignments: list[Assignment]
    unserved: list[int | str]  # lots not shipped
    solve_seconds: float = 0.0
    status: str = "ok"  # optimizer: OPTIMAL | FEASIBLE | ...
    notes: list[str] = field(default_factory=list)


def approximate_distance(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> tuple[float, float, str]:
    """Straight-line km x road factor at the fallback speed (tracking.routing.straight_line): no OSRM needed.
    Results using it are labelled 'approximate distances'."""
    from tracking.routing import straight_line

    r = straight_line(o_lat, o_lon, d_lat, d_lon, steps=1)
    return r.distance_km, r.duration_min, r.source


def road_distance(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> tuple[float, float, str]:
    """OSRM road distance when configured, else the straight-line fallback (tracking.routing.road_km)."""
    from tracking.routing import road_km

    return road_km(o_lat, o_lon, d_lat, d_lon)
