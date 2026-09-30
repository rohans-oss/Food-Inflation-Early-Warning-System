"""Score ANY plan with one cost model (rule 21: decisions are compared on decisions, not forecast accuracy).

`prices` gives the Rs/quintal each mandi is scored at. For planning numbers pass the p50 forecast; for the studies
pass the price that was ACTUALLY realised, so no method is graded with the forecast it planned on.
V3-1: a truck may carry several lots (pickup order = Assignment.seq) and make a second, return trip (trip = 2).
Its cost is its whole day's route; each lot's spoilage runs from ITS pickup to its mandi. One lot per truck gives
exactly the V3-0 numbers."""
from .model import Plan, Problem


def evaluate(problem: Problem, plan: Plan, prices: dict) -> dict:
    lots = {x.id: x for x in problem.lots}
    mandis = {x.id: x for x in problem.mandis}
    vehicles = {x.id: x for x in problem.vehicles}
    net = gross_total = transport = spoil_rs = km = empty_km = shipped = 0.0
    spoil_viol, spoil_viol_t, cap_viol_t, no_vehicle, driver_over, trucks = 0, 0.0, 0.0, 0, 0, 0
    shared_loads = return_trips = 0
    into, rows, by_vehicle = {}, [], {}
    for a in plan.assignments:
        if a.vehicle_id is None or a.vehicle_id not in vehicles:
            no_vehicle += 1
            continue
        by_vehicle.setdefault(a.vehicle_id, []).append(a)
    for vid, assigned in by_vehicle.items():
        v = vehicles[vid]
        trips = []
        for trip in sorted({a.trip for a in assigned}):
            legs = sorted((a for a in assigned if a.trip == trip), key=lambda a: a.seq)
            if len({a.mandi_id for a in legs}) != 1:
                raise ValueError(f"vehicle {vid} trip {trip} goes to more than one mandi")
            load_t = sum(lots[a.lot_id].tons for a in legs)
            if v.capacity_tons + 1e-9 < load_t:
                raise ValueError(f"plan puts {load_t} t on a {v.capacity_tons} t vehicle")
            trips.append((mandis[legs[0].mandi_id], [lots[a.lot_id] for a in legs]))
            shared_loads += len(legs) > 1
            return_trips += trip > 1
        r = problem.route(v, trips)
        cost = r["km"] * problem.rate_per_km(v)
        trucks += 1
        transport += cost
        km += r["km"]
        empty_km += r["empty_km"]
        driver_over += (r["minutes"] > problem.max_driver_minutes + 1e-6)
        day_t = sum(x.tons for _, ls in trips for x in ls)
        for m, ls in trips:
            for lot in ls:
                sp = problem.spoilage_after(lot, m, r["clock"][lot.id])
                gross = prices[m.id] * lot.tons * 10
                share = cost * lot.tons / day_t  # the truck's day cost, split by tonnes (for per-lot rows only)
                n = gross * (1 - sp / 100) - share
                net += n
                gross_total += gross
                spoil_rs += gross * sp / 100
                shipped += lot.tons
                into[m.id] = into.get(m.id, 0.0) + lot.tons
                if sp > problem.max_spoilage + 1e-9:
                    spoil_viol += 1
                    spoil_viol_t += lot.tons
                rows.append({"lot": lot.id, "mandi": m.name, "vehicle": vid, "tons": lot.tons,
                             "spoilage_pct": round(sp, 2), "trip_cost": round(share), "net": round(n)})
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
        "spoilage_loss": round(spoil_rs), "vehicle_km": round(km, 1), "empty_km": round(empty_km, 1),
        "trucks_used": trucks, "shared_loads": shared_loads, "return_trips": return_trips,
        "tons_shipped": round(shipped, 2), "lots_shipped": len(rows), "lots_unserved": len(plan.unserved),
        "tons_unserved": round(unserved_t, 2),
        "violations_spoilage_lots": spoil_viol, "violations_spoilage_tons": round(spoil_viol_t, 2),
        "violations_mandi_over": over_mandis, "violations_mandi_over_tons": round(cap_viol_t, 2),
        "violations_total": spoil_viol + over_mandis + no_vehicle,
        "driver_day_over": driver_over,  # reported, not a violation: V3-0 had no driver-day limit
        "assignments": rows,
    }
