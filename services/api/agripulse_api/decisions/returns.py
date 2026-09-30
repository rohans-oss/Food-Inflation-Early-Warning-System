"""V3-1: return loads. After its first delivery a truck normally drives home empty. Instead it may take ONE more
lot that is still waiting: drive from the first mandi to that lot, sell it at a mandi, then go home.

Worth it only if the lot's net value covers the EXTRA km (first mandi -> lot -> second mandi -> home, instead of
first mandi -> home), the whole day stays within max_driver_hours, the lot's spoilage stays under the cap, and the
second mandi still has room after the first trips. CP-SAT matches trucks to waiting lots (each at most once)."""
import time

from ortools.sat.python import cp_model

from .model import Assignment, Plan, Problem
from .optimizer import STATUS, candidates, make_solver


def add_return_loads(problem: Problem, plan: Plan, objective: str = "p50", time_limit_s: float | None = None) -> Plan:
    t0 = time.perf_counter()
    lots = {x.id: x for x in problem.lots}
    mandis = {x.id: x for x in problem.mandis}
    vehicles = {x.id: x for x in problem.vehicles}
    first = {}
    for a in plan.assignments:
        if a.vehicle_id in vehicles and a.trip == 1:
            first.setdefault(a.vehicle_id, []).append(a)
    into = {}
    for a in plan.assignments:
        into[a.mandi_id] = into.get(a.mandi_id, 0.0) + lots[a.lot_id].tons
    waiting = [lots[i] for i in plan.unserved]
    model = cp_model.CpModel()
    x, value = {}, {}
    for vid, legs in first.items():
        v = vehicles[vid]
        legs.sort(key=lambda a: a.seq)
        m1 = mandis[legs[0].mandi_id]
        trip1 = (m1, [lots[a.lot_id] for a in legs])
        base = problem.route(v, [trip1])
        home = problem.leg(m1.lat, m1.lon, v.lat, v.lon)[0] if problem.cfg["transport"]["vehicle"].get(
            "count_return_leg", True) else 0.0
        for lot in waiting:
            if lot.tons > v.capacity_tons + 1e-9:
                continue
            for m2 in candidates(problem, lot):
                r = problem.route(v, [trip1, (m2, [lot])])
                if r["minutes"] > problem.max_driver_minutes:
                    continue
                sp = problem.spoilage_after(lot, m2, r["clock"][lot.id])
                if sp > problem.max_spoilage + 1e-9:
                    continue
                price = m2.p50 if objective == "p50" else m2.p10
                extra_km = r["km"] - base["km"]
                val = round(price * lot.tons * 10 * (1 - sp / 100) - extra_km * problem.rate_per_km(v))
                if val <= 0:
                    continue
                key = (vid, lot.id, m2.id)
                x[key] = model.NewBoolVar(f"r{len(x)}")
                value[key] = val
    by_v, by_lot, by_m = {}, {}, {}
    for key, var in x.items():
        by_v.setdefault(key[0], []).append(var)
        by_lot.setdefault(key[1], []).append(var)
        by_m.setdefault(key[2], []).append((lots[key[1]].tons, var))
    for vs in list(by_v.values()) + list(by_lot.values()):
        model.AddAtMostOne(vs)
    for mid, pairs in by_m.items():
        cap = problem.mandi_cap(mandis[mid])
        if cap is not None:
            room = cap - into.get(mid, 0.0)
            model.Add(sum(round(t * 100) * var for t, var in pairs) <= round(max(room, 0.0) * 100))
    model.Maximize(sum(value[k] * var for k, var in x.items()))
    solver = make_solver(problem.cfg, time_limit_s)
    st = solver.Solve(model)
    extra = []
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        extra = [Assignment(k[1], k[2], k[0], seq=0, trip=2) for k, var in x.items() if solver.Value(var)]
    taken = {a.lot_id for a in extra}
    return Plan(plan.method + "+returns", plan.assignments + extra, [i for i in plan.unserved if i not in taken],
                plan.solve_seconds + time.perf_counter() - t0, f"{plan.status}+{STATUS.get(st, str(st))}",
                plan.notes + [f"{len(extra)} return loads from {len(x)} options"])
