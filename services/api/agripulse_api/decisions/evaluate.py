"""Score ANY plan with one cost model (rule 21: decisions are compared on decisions, not forecast accuracy).

`prices` gives the Rs/quintal each mandi is scored at. For planning numbers pass the p50 forecast; for the V3-0 study
pass the price that was ACTUALLY realised, so neither method is graded with the forecast it planned on."""
from .model import Plan, Problem


def evaluate(problem: Problem, plan: Plan, prices: dict) -> dict:
    lots = {x.id: x for x in problem.lots}
    mandis = {x.id: x for x in problem.mandis}
    vehicles = {x.id: x for x in problem.vehicles}
    net = gross_total = transport = spoil_rs = km = shipped = 0.0
    spoil_viol, spoil_viol_t, cap_viol_t, no_vehicle = 0, 0.0, 0.0, 0
    into = {}
    rows = []
    for a in plan.assignments:
        lot, m = lots[a.lot_id], mandis[a.mandi_id]
        v = vehicles.get(a.vehicle_id)
        if v is None:
            no_vehicle += 1
            continue
        if v.capacity_tons + 1e-9 < lot.tons:
            raise ValueError(f"plan puts {lot.tons} t on a {v.capacity_tons} t vehicle")
        sp = problem.spoilage(lot, m)
        gross = prices[m.id] * lot.tons * 10
        cost = problem.trip_cost(v, lot, m)
        n = gross * (1 - sp / 100) - cost
        net += n
        gross_total += gross
        transport += cost
        spoil_rs += gross * sp / 100
        km += problem.vehicle_km(v, lot, m)
        shipped += lot.tons
        into[m.id] = into.get(m.id, 0.0) + lot.tons
        if sp > problem.max_spoilage + 1e-9:
            spoil_viol += 1
            spoil_viol_t += lot.tons
        rows.append({"lot": a.lot_id, "mandi": m.name, "vehicle": a.vehicle_id, "tons": lot.tons,
                     "spoilage_pct": round(sp, 2), "trip_cost": round(cost), "net": round(n)})
    over_mandis = 0
    for mid, t in into.items():
        cap = problem.mandi_cap(mandis[mid])
        if cap is not None and t > cap + 1e-9:
            over_mandis += 1
            cap_viol_t += t - cap
    unserved_t = sum(lots[i].tons for i in plan.unserved)
    return {
        "method": plan.method, "status": plan.status, "solve_seconds": round(plan.solve_seconds, 3),
        "net_value": round(net), "gross": round(gross_total), "transport_cost": round(transport),
        "spoilage_loss": round(spoil_rs), "vehicle_km": round(km, 1),
        "tons_shipped": round(shipped, 2), "lots_shipped": len(rows), "lots_unserved": len(plan.unserved),
        "tons_unserved": round(unserved_t, 2),
        "violations_spoilage_lots": spoil_viol, "violations_spoilage_tons": round(spoil_viol_t, 2),
        "violations_mandi_over": over_mandis, "violations_mandi_over_tons": round(cap_viol_t, 2),
        "violations_total": spoil_viol + over_mandis + no_vehicle,
        "assignments": rows,
    }
