"""V3-1: shared truckloads. Several lots on one truck when that pays, under the same hard limits as V3-0.

    1. candidate loads: every lot alone, plus groups of up to `max_lots_per_load` lots taken from each lot's
       `neighbours` nearest pickups within `cluster_radius_km`, that fit on some truck
    2. per (load, mandi): the best pickup order, found exactly (<= 4! orders)
    3. CP-SAT picks (load, truck, mandi): each lot at most once, each truck one outbound trip, mandi room,
       per-lot spoilage (each lot's own time on board) <= cap, driver day <= max for SHARED loads
Single lots get exactly V3-0's options (all its candidate mandis, every truck that fits, no driver-day limit),
so any plan V3-0 can make is still available: sharing is only chosen when it is worth more.
`hired=True` (FPO planning): no fleet is known; each load gets the smallest standard truck that fits, hired at its
first pickup, as many as needed."""
import itertools
import time

from ortools.sat.python import cp_model

from .model import Assignment, Plan, Problem, Vehicle
from .optimizer import STATUS, candidates, make_solver


def order_pickups(problem: Problem, lots: list, m) -> tuple[list, float]:
    """Pickup order minimising road km from the first pickup through all pickups to the mandi."""
    best, best_km = None, float("inf")
    for perm in itertools.permutations(lots):
        km = sum(problem.leg(a.lat, a.lon, b.lat, b.lon)[0] for a, b in zip(perm, perm[1:]))
        km += problem.leg(perm[-1].lat, perm[-1].lon, m.lat, m.lon)[0]
        if km < best_km:
            best, best_km = list(perm), km
    return best, best_km


def candidate_loads(problem: Problem, max_tons: float) -> list[tuple]:
    """Tuples of lot ids: every lot alone, plus nearby groups that fit in `max_tons`."""
    c = problem.cfg["consolidation"]
    k, radius, size = int(c["neighbours"]), float(c["cluster_radius_km"]), int(c["max_lots_per_load"])
    lots = {x.id: x for x in problem.lots}
    out = {(x.id,) for x in problem.lots}
    for a in problem.lots:
        near = sorted(((problem.leg(a.lat, a.lon, b.lat, b.lon)[0], b.id) for b in problem.lots if b.id != a.id))
        near = [i for d, i in near if d <= radius][:k]
        for n in range(1, size):
            for combo in itertools.combinations(near, n):
                ids = tuple(sorted((a.id, *combo), key=str))
                if sum(lots[i].tons for i in ids) <= max_tons + 1e-9:
                    out.add(ids)
    return sorted(out, key=lambda t: (len(t), [str(i) for i in t]))


def hired_truck(problem: Problem, tons: float, lat: float, lon: float, key) -> Vehicle | None:
    sizes = sorted(problem.cfg["transport"]["vehicle"].get("sizes_tons", [2.5, 5, 9, 10, 16]))
    cap = next((s for s in sizes if s >= tons - 1e-9), None)
    return None if cap is None else Vehicle(f"hired-{key}", cap, lat, lon)


def optimize_loads(problem: Problem, objective: str | None = None, time_limit_s: float | None = None,
                   hired: bool = False) -> tuple[Plan, list[Vehicle]]:
    """Returns the plan and the trucks it uses (hired trucks are created here)."""
    ocfg, ccfg = problem.cfg["optimizer"], problem.cfg["consolidation"]
    objective = objective or ocfg["objective"]
    if objective not in ("p50", "p10"):
        raise ValueError("objective must be 'p50' or 'p10'")
    t0 = time.perf_counter()
    lots = {x.id: x for x in problem.lots}
    sizes = problem.cfg["transport"]["vehicle"].get("sizes_tons", [16])
    max_tons = max(sizes) if hired else max((v.capacity_tons for v in problem.vehicles), default=0.0)
    model = cp_model.CpModel()
    x, value, meta = {}, {}, {}
    hired_trucks: dict = {}
    single_opts = {i: candidates(problem, lots[i]) for i in lots}
    for load in candidate_loads(problem, max_tons):
        ls = [lots[i] for i in load]
        tons = sum(z.tons for z in ls)
        shared = len(ls) > 1
        if shared:  # mandis every lot of the group may go to, best few by value per tonne
            common = [m for m in single_opts[load[0]] if all(m in single_opts[i] for i in load[1:])]
            common.sort(key=lambda m: -(m.p50 - problem.leg(ls[0].lat, ls[0].lon, m.lat, m.lon)[0]))
            mandis = common[: int(ccfg["mandis_per_load"])]
        else:
            mandis = single_opts[load[0]]
        for m in mandis:
            order, _ = order_pickups(problem, ls, m) if shared else (ls, 0.0)
            if hired:
                v = hired_truck(problem, tons, order[0].lat, order[0].lon, "-".join(map(str, load)) + f"-{m.id}")
                trucks = [v] if v else []
            else:
                fit = [v for v in problem.vehicles if v.capacity_tons + 1e-9 >= tons]
                if shared:
                    fit.sort(key=lambda v: problem.leg(v.lat, v.lon, order[0].lat, order[0].lon)[0])
                    fit = fit[: int(ccfg["vehicles_per_load"])]
                trucks = fit
            price = m.p50 if objective == "p50" else m.p10
            for v in trucks:
                r = problem.route(v, [(m, order)])
                if shared and r["minutes"] > problem.max_driver_minutes:
                    continue
                sp = {z.id: problem.spoilage_after(z, m, r["clock"][z.id]) for z in order}
                if any(s > problem.max_spoilage + 1e-9 for s in sp.values()):
                    continue
                val = round(sum(price * z.tons * 10 * (1 - sp[z.id] / 100) for z in order)
                            - r["km"] * problem.rate_per_km(v))
                if val <= 0:
                    continue
                key = (load, m.id, v.id)
                x[key] = model.NewBoolVar(f"x{len(x)}")
                value[key], meta[key] = val, (order, v)
                if hired:
                    hired_trucks[v.id] = v
    by_lot, by_veh, by_mandi = {}, {}, {}
    for key, var in x.items():
        load, mid, vid = key
        for i in load:
            by_lot.setdefault(i, []).append(var)
        if not hired:
            by_veh.setdefault(vid, []).append(var)
        by_mandi.setdefault(mid, []).append((sum(lots[i].tons for i in load), var))
    for vs in list(by_lot.values()) + list(by_veh.values()):
        model.AddAtMostOne(vs)
    for m in problem.mandis:
        cap = problem.mandi_cap(m)
        if cap is not None and m.id in by_mandi:
            model.Add(sum(round(t * 100) * var for t, var in by_mandi[m.id]) <= round(cap * 100))
    model.Maximize(sum(value[k] * var for k, var in x.items()))
    solver = make_solver(problem.cfg, time_limit_s)
    st = solver.Solve(model)
    assignments, used = [], []
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for key, var in x.items():
            if solver.Value(var):
                order, v = meta[key]
                used.append(v)
                assignments += [Assignment(z.id, key[1], v.id, seq=i) for i, z in enumerate(order)]
    served = {a.lot_id for a in assignments}
    plan = Plan("optimizer_shared" + ("_p10" if objective == "p10" else ""), assignments,
                [i for i in lots if i not in served], time.perf_counter() - t0, STATUS.get(st, str(st)))
    plan.notes.append(f"{len(x)} options over {len({k[0] for k in x})} candidate loads")
    return plan, used
