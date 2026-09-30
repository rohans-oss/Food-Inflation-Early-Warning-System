"""OR-Tools CP-SAT: assign every lot to (mandi, vehicle) at once.

    maximise  sum x[l,m,v] * net(l, m, v)        net = price x q x (1 - spoilage%) - vehicle trip cost
    s.t.      each lot at most once              (a lot may stay unshipped if no option pays)
              each vehicle at most one trip      (V3-0: one lot per truck; V3-1 adds consolidation)
              vehicle capacity >= lot tonnes     (only such pairs exist)
              spoilage%(l, m) <= max_spoilage    (only such pairs exist)
              sum tonnes into mandi m <= mandi_extra_share x its typical daily arrivals
Price = the calibrated p50 ("p50" objective) or p10 (risk-averse "p10" objective) of the display-model forecast.
Money is scaled to whole rupees and tonnes to 10 kg for the integer model."""
import time

from ortools.sat.python import cp_model

from .model import Assignment, Plan, Problem

STATUS = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE",
          cp_model.MODEL_INVALID: "MODEL_INVALID", cp_model.UNKNOWN: "UNKNOWN"}


def candidates(problem: Problem, lot):
    """Mandis this lot may go to: within the spoilage cap, nearest `max_mandis_per_lot` by road km."""
    k = problem.cfg["optimizer"]["max_mandis_per_lot"]
    ok = [m for m in problem.mandis if problem.spoilage(lot, m) <= problem.max_spoilage]
    ok.sort(key=lambda m: problem.leg(lot.lat, lot.lon, m.lat, m.lon)[0])
    return ok[:k]


def optimize(problem: Problem, objective: str | None = None, time_limit_s: float | None = None,
             fixed_mandis: dict | None = None) -> Plan:
    """`fixed_mandis` (lot id -> MandiOption): only choose trucks for mandis decided elsewhere, with no spoilage or
    mandi cap. Used by the study to split the optimizer's gain into 'truck assignment' vs 'mandi choice'."""
    ocfg = problem.cfg["optimizer"]
    objective = objective or ocfg["objective"]
    if objective not in ("p50", "p10"):
        raise ValueError("objective must be 'p50' or 'p10'")
    t0 = time.perf_counter()
    model = cp_model.CpModel()
    x, value = {}, {}
    for lot in problem.lots:
        options = candidates(problem, lot) if fixed_mandis is None else [fixed_mandis[lot.id]]
        for m in options:
            price = m.p50 if objective == "p50" else m.p10
            for v in problem.vehicles:
                if v.capacity_tons + 1e-9 < lot.tons:
                    continue
                val = round(problem.net(lot, m, v, price))
                if val <= 0:
                    continue  # never worth doing: leaving the lot unshipped is better
                key = (lot.id, m.id, v.id)
                x[key] = model.NewBoolVar(f"x_{lot.id}_{m.id}_{v.id}")
                value[key] = val
    by_lot, by_veh, by_mandi = {}, {}, {}
    tons = {lot.id: lot.tons for lot in problem.lots}
    for key, var in x.items():
        by_lot.setdefault(key[0], []).append(var)
        by_veh.setdefault(key[2], []).append(var)
        by_mandi.setdefault(key[1], []).append((key[0], var))
    for vs in by_lot.values():
        model.AddAtMostOne(vs)
    for vs in by_veh.values():
        model.AddAtMostOne(vs)
    for m in problem.mandis if fixed_mandis is None else []:
        cap = problem.mandi_cap(m)
        if cap is not None and m.id in by_mandi:
            model.Add(sum(round(tons[l] * 100) * var for l, var in by_mandi[m.id]) <= round(cap * 100))
    model.Maximize(sum(value[k] * var for k, var in x.items()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s or ocfg["time_limit_s"])
    solver.parameters.num_workers = int(ocfg.get("workers", 8))
    solver.parameters.random_seed = 1
    st = solver.Solve(model)
    status = STATUS.get(st, str(st))
    assignments = []
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        assignments = [Assignment(k[0], k[1], k[2]) for k, var in x.items() if solver.Value(var)]
    served = {a.lot_id for a in assignments}
    notes = [] if x else ["no feasible (lot, mandi, vehicle) option pays for itself"]
    name = "rule_mandis_opt_trucks" if fixed_mandis is not None else ("optimizer" if objective == "p50" else "optimizer_p10")
    return Plan(name, assignments,
                [lot.id for lot in problem.lots if lot.id not in served], time.perf_counter() - t0, status, notes)
