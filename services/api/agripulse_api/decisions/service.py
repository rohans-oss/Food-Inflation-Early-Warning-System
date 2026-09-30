"""V3-0: the recommenders on live data. Builds a Problem from the database (display-model forecasts, weather,
typical arrivals minus tonnes already on the road) and runs the rule or the optimizer.
Which one users get is `[recommender] default` in config/recommender.toml; the other stays callable (Admin)."""
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracking.geo import haversine_km

from ..models import Mandi
from ..provenance import worst
from ..supply import _latest_forecast, _temp_c, cost_config, in_transit, recommend, typical_daily_arrivals
from . import evaluate, optimize, rule_plan
from .model import Lot, MandiOption, Problem, Vehicle, road_distance
from .sample import DENSITY, scenario

RECOMMENDERS = ("rule", "optimizer")


def default_recommender() -> str:
    d = cost_config().get("recommender", {}).get("default", "rule")
    if d not in RECOMMENDERS:
        raise ValueError(f"[recommender] default must be one of {RECOMMENDERS}")
    return d


def mandi_options(db: Session, weeks: int = 1, near: list[tuple[float, float]] | None = None) -> list[MandiOption]:
    """Mandis with a display-model forecast. Absorption room = typical daily arrivals; tonnes already moving there
    are subtracted by `absorption_room`."""
    cfg = cost_config()
    radius = cfg["search"]["radius_km"]
    out = []
    for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None))):
        if near and min(haversine_km(a, b, m.lat, m.lon) for a, b in near) > radius:
            continue
        f = _latest_forecast(db, m.id, weeks)
        if f is None:
            continue
        typical = typical_daily_arrivals(db, m.id)
        out.append(MandiOption(m.id, m.name, m.lat, m.lon, f.p10, f.p50, f.p90, _temp_c(db, m.id)[0],
                               typical["tons"], f.data_provenance))
    return out


def absorption_room(db: Session, problem: Problem) -> dict:
    """Per mandi: cap (share x typical daily arrivals) minus tonnes already on the road to it (V1 tracking)."""
    moving = in_transit(db)
    out = {}
    for m in problem.mandis:
        cap = problem.mandi_cap(m)
        out[m.id] = None if cap is None else round(cap - moving.get(m.id, {}).get("tons_in_transit", 0.0), 2)
    return out


def hired_truck(lot: Lot, cfg: dict) -> Vehicle:
    """Single-lot advice: the smallest standard truck that fits, hired at the pickup point."""
    sizes = sorted(cfg["transport"]["vehicle"].get("sizes_tons", [2.5, 5, 9, 10, 16]))
    cap = next((s for s in sizes if s >= lot.tons), max(sizes))
    return Vehicle("hired", cap, lot.lat, lot.lon)


def recommend_single(db: Session, lat: float, lon: float, tons: float, weeks: int = 1) -> dict:
    """/recommend/best-mandi. 'rule' = V1 unchanged. 'optimizer' = same ranking data, re-scored with the V3 cost
    model (hired truck, return leg) and hard limits: spoilage cap and the mandi's remaining absorption room.
    For ONE lot the CP-SAT model reduces to 'best feasible option', so it is computed directly here."""
    base = recommend(db, lat, lon, tons, weeks=weeks)
    which = default_recommender()
    base["recommender"] = which
    if which == "rule":
        return base
    cfg = cost_config()
    lot = Lot("farmer", lat, lon, tons)
    mandis = {m.id: m for m in mandi_options(db, weeks, near=[(lat, lon)])}
    prob = Problem([lot], [hired_truck(lot, cfg)], list(mandis.values()), road_distance, cfg)
    room = absorption_room(db, prob)
    truck = prob.vehicles[0]
    for r in base["ranked"]:
        m = mandis.get(r["mandi_id"])
        if m is None:
            continue
        sp = prob.spoilage(lot, m)
        cost = prob.trip_cost(truck, lot, m)
        r["transport_cost"] = round(cost)
        r["spoilage_pct"] = round(sp, 2)
        r["net_value"] = {q: round(prob.net(lot, m, truck, getattr(m, q))) for q in ("p10", "p50", "p90")}
        why = []
        if sp > prob.max_spoilage:
            why.append(f"expected transit loss {sp:.1f}% is above the {prob.max_spoilage:g}% limit")
        if room.get(m.id) is not None and tons > room[m.id]:
            why.append(f"this mandi can take about {max(room[m.id], 0):.0f} t more today (typical arrivals, minus "
                       "trucks already on the way)")
        r["feasible"], r["why_not"] = not why, why
    base["ranked"].sort(key=lambda r: (not r.get("feasible", True), -r["net_value"]["p50"]))
    for i, r in enumerate(base["ranked"]):
        r["rank"] = i + 1
        nxt = base["ranked"][i + 1] if i + 1 < len(base["ranked"]) else None
        r["clearly_better_than_next"] = bool(nxt) and r["net_value"]["p10"] > nxt["net_value"]["p90"]
    base["vehicle_assumption"] = {"capacity_tons": truck.capacity_tons, "rate_per_km": prob.rate_per_km(truck),
                                  "return_leg": cfg["transport"]["vehicle"].get("count_return_leg", True)}
    base["formula"] = ("net = p50 x quantity x (1 - spoilage%) - truck km (to mandi and back) x rate per km; "
                       f"excluded: spoilage above {prob.max_spoilage:g}%, or more tonnes than the mandi can absorb")
    return base


def compare(db: Session, density: str = "medium", seed: int = 1, weeks: int = 1) -> dict:
    """Admin: run BOTH recommenders on one SIMULATED batch of lots and trucks against today's real mandis and
    display-model forecasts. Scored at the forecast p50 (nothing has happened yet): a planning comparison, not proof."""
    if density not in DENSITY:
        raise ValueError(f"density must be one of {list(DENSITY)}")
    lots, vehicles = scenario(np.random.default_rng([seed, 0, list(DENSITY).index(density)]), density)
    mandis = mandi_options(db, weeks, near=[(x.lat, x.lon) for x in lots])
    if not mandis:
        return {"error": "No mandi has a display-model forecast yet. Run the forecast job first."}
    prob = Problem(lots, vehicles, mandis, road_distance, cost_config())
    p50 = {m.id: m.p50 for m in mandis}
    results = []
    for plan in (rule_plan(prob), optimize(prob, "p50"), optimize(prob, "p10")):
        e = evaluate(prob, plan, p50)
        e.pop("assignments")
        results.append(e)
    sources = {prob.leg(x.lat, x.lon, m.lat, m.lon)[2] for x in lots[:3] for m in mandis[:3]}
    return {"density": density, "seed": seed, "n_lots": len(lots), "n_vehicles": len(vehicles),
            "tons_offered": round(sum(x.tons for x in lots), 1),
            "vehicle_capacity": round(sum(v.capacity_tons for v in vehicles), 1),
            "default_recommender": default_recommender(), "results": results,
            "scored_at": "forecast p50",
            "distances": "road (OSRM)" if sources == {"osrm"} else "approximate (straight-line x road factor)",
            "lots_are_simulated": True,
            "mandis": len(mandis), "mandis_with_known_room": sum(m.typical_daily_tons is not None for m in mandis),
            "data_provenance": worst(*(m.data_provenance for m in mandis))}
