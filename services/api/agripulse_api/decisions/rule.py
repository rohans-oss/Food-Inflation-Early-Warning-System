"""The OLD way (V1): each lot goes to its own top-ranked mandi, as /recommend/best-mandi ranks it. V1 never assigned
trucks (the fleet owner did), so the baseline adds a sensible dispatcher: largest lot first, the NEAREST free truck
that fits. (Chosen before the V3-0 study ran; a smallest-truck-first dispatcher ignored distance and would have made
the optimizer look better than it is.)
No vehicle capacity in the ranking, no spoilage cap, no mandi absorption limit: that is what V1 did."""
import time

from .model import Assignment, Plan, Problem


def v1_rank(problem: Problem, lot):
    """V1 ranking: net = p50 x quantity - road km x rate_per_km_ton x tons - spoilage% x gross (supply.recommend)."""
    rate = problem.cfg["transport"]["rate_per_km_ton"]
    out = []
    for m in problem.mandis:
        km = problem.leg(lot.lat, lot.lon, m.lat, m.lon)[0]
        gross = m.p50 * lot.tons * 10
        out.append((gross - km * rate * lot.tons - gross * problem.spoilage(lot, m) / 100, m))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def v1_choices(problem: Problem) -> dict:
    """lot id -> the mandi V1 ranks first for it."""
    return {lot.id: v1_rank(problem, lot)[0][1] for lot in problem.lots} if problem.mandis else {}


def rule_plan(problem: Problem) -> Plan:
    t0 = time.perf_counter()
    choice = v1_choices(problem)
    free = list(problem.vehicles)
    assignments, unserved = [], []
    for lot in sorted(problem.lots, key=lambda x: x.tons, reverse=True):
        m = choice.get(lot.id)
        fits = [v for v in free if v.capacity_tons >= lot.tons]
        if m is None or not fits:
            unserved.append(lot.id)
            continue
        v = min(fits, key=lambda v: (problem.leg(v.lat, v.lon, lot.lat, lot.lon)[0], v.capacity_tons))
        free.remove(v)
        assignments.append(Assignment(lot.id, m.id, v.id))
    return Plan("rule", assignments, unserved, time.perf_counter() - t0)
