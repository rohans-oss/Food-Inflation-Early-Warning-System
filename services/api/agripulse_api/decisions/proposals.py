"""V3-1 in the product: proposals a person accepts or rejects (never applied automatically).

  FPO          plan_fpo_loads     its registered, ungrouped lots -> shared truckloads (hired trucks), saving vs one
                                  hired truck per lot. Accept = one shipment per load (make_shipment, audited).
  Fleet owner  fleet_return_loads trucks that delivered today + this fleet's booked shipments with no trip yet ->
                                  one job for each truck's drive home. Accept = trips (make_trip, audited).
Scope: a proposal belongs to one org and only ever contains that org's lots / trips / shipments booked with it.
Gated by [consolidation] enabled and [return_loads] enabled (config/recommender.toml, pre-registered switch)."""
import copy
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from ortools.sat.python import cp_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, GeofenceEvent, Lot as LotRow, LoadProposal, Shipment, Trip, User
from ..provenance import worst
from ..scoping import lot_filter
from ..supply import cost_config, ist_today
from .evaluate import evaluate
from .loads import optimize_loads, order_pickups
from .model import Lot, MandiOption, Problem, Vehicle, road_distance
from .optimizer import make_solver


SECTION = {"consolidation": "consolidation", "return_load": "return_loads"}


def enabled(kind: str) -> bool:
    return bool(cost_config().get(SECTION[kind], {}).get("enabled", False))


def _require_enabled(kind: str):
    if not enabled(kind):
        raise HTTPException(409, f"{kind.replace('_', ' ')} proposals are switched off "
                                 f"([{SECTION[kind]}] enabled in config/recommender.toml)")


def proposal_out(p: LoadProposal) -> dict:
    return {"id": p.id, "kind": p.kind, "status": p.status, "created_at": p.created_at, "est_saving": round(p.est_saving),
            "decided_at": p.decided_at, "decided_by": p.decided_by, "reject_reason": p.reject_reason,
            "data_provenance": p.data_provenance, "is_simulated": p.is_simulated, **p.payload}


def _distances_label(prob: Problem) -> str:
    srcs = {src for (_, _, src) in prob._km.values()}
    return "road (OSRM)" if srcs == {"osrm"} else "approximate (straight-line x road factor)"


# ---------------------------------------------------------------- FPO: shared loads


def plan_fpo_loads(db: Session, user: User, weeks: int = 1) -> LoadProposal:
    from .service import mandi_options

    _require_enabled("consolidation")
    if user.org_id is None:
        raise HTTPException(400, "Plan loads from an FPO account")
    rows = db.scalars(select(LotRow).where(lot_filter(user), LotRow.status == "registered", LotRow.shipment_id.is_(None))).all()
    if not rows:
        raise HTTPException(409, "No registered lots waiting to be grouped")
    lots = [Lot(r.id, r.pickup_lat, r.pickup_lon, r.quantity_tons, r.crop) for r in rows]
    mandis = mandi_options(db, weeks, near=[(x.lat, x.lon) for x in lots])
    if not mandis:
        raise HTTPException(409, "No nearby mandi has a forecast yet")
    cfg = cost_config()
    prob = Problem(lots, [], mandis, road_distance, cfg)
    cap = cfg["optimizer"]["time_limit_s"]
    plan, trucks = optimize_loads(prob, hired=True, time_limit_s=cap)
    one = copy.deepcopy(cfg)
    one["consolidation"]["max_lots_per_load"] = 1  # baseline: every lot on its own hired truck
    base_prob = Problem(lots, [], mandis, road_distance, one)
    base_plan, base_trucks = optimize_loads(base_prob, hired=True, time_limit_s=cap)
    p50 = {m.id: m.p50 for m in mandis}
    e = evaluate(Problem(lots, trucks, mandis, road_distance, cfg), plan, p50)
    b = evaluate(Problem(lots, base_trucks, mandis, road_distance, cfg), base_plan, p50)
    names = {m.id: m.name for m in mandis}
    caps = {v.id: v.capacity_tons for v in trucks}
    tons = {x.id: x.tons for x in lots}
    loads = {}
    for a in sorted(plan.assignments, key=lambda a: (str(a.vehicle_id), a.seq)):
        L = loads.setdefault(a.vehicle_id, {"lot_ids": [], "mandi_id": a.mandi_id, "mandi": names[a.mandi_id],
                                            "truck_tons": caps[a.vehicle_id], "tons": 0.0})
        L["lot_ids"].append(a.lot_id)
        L["tons"] = round(L["tons"] + tons[a.lot_id], 2)
    per_lot = {r["lot"]: r for r in e["assignments"]}
    out_loads = []
    for L in loads.values():
        L["shared"] = len(L["lot_ids"]) > 1
        L["est_net_p50"] = sum(per_lot[i]["net"] for i in L["lot_ids"])
        L["est_transport"] = sum(per_lot[i]["trip_cost"] for i in L["lot_ids"])
        out_loads.append(L)
    out_loads.sort(key=lambda L: (not L["shared"], -L["tons"]))
    saving = e["net_value"] - b["net_value"]
    prop = LoadProposal(
        kind="consolidation", org_id=user.org_id, created_by=user.id, est_saving=float(saving),
        is_simulated=any(r.is_simulated for r in rows), data_provenance=worst(*(m.data_provenance for m in mandis)),
        payload={"loads": out_loads, "unserved_lot_ids": plan.unserved,
                 "totals": {k: e[k] for k in ("net_value", "transport_cost", "spoilage_loss", "trucks_used",
                                              "shared_loads", "lots_shipped", "vehicle_km")},
                 "baseline": {"what": "every lot on its own hired truck",
                              **{k: b[k] for k in ("net_value", "transport_cost", "trucks_used", "vehicle_km")}},
                 "scored_at": "forecast p50", "distances": _distances_label(prob), "solver_status": plan.status})
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


# ---------------------------------------------------------------- fleet owner: return loads


def _delivered_today(db: Session, t: Trip, today) -> bool:
    if t.delivery_scanned_at and (t.delivery_scanned_at + timedelta(hours=5, minutes=30)).date() == today:
        return True
    ev = db.scalar(select(GeofenceEvent.occurred_at).where(GeofenceEvent.trip_id == t.id,
                                                           GeofenceEvent.event == "reached_mandi").limit(1))
    if ev is None:
        return False
    ev = ev if ev.tzinfo else ev.replace(tzinfo=timezone.utc)
    return (ev + timedelta(hours=5, minutes=30)).date() == today


def fleet_return_loads(db: Session, user: User) -> LoadProposal:
    """Trucks of THIS fleet that delivered today (reached the mandi or delivery QR scanned), with no other trip, x
    shipments booked with THIS fleet that have no trip yet. Home = where the delivering trip started (vehicles have
    no stored base). Saving = empty km avoided: (mandi -> home) + a separate truck's round trip for the job, minus
    (mandi -> pickups -> job mandi -> home)."""
    _require_enabled("return_load")
    if user.role != "admin" and (user.role != "fleet_owner" or user.org_id is None):
        raise HTTPException(403, "Return loads are proposed to fleet owners")
    cfg = cost_config()
    org = user.org_id
    today = ist_today()
    active = {"assigned", "accepted", "in_progress"}
    trips = db.scalars(select(Trip).where(Trip.fleet_org_id == org, Trip.status.in_(["in_progress", "completed"]))).all()
    busy = {t.vehicle_id for t in db.scalars(select(Trip).where(Trip.fleet_org_id == org, Trip.status.in_(active)))
            if not _delivered_today(db, t, today)}
    trucks = [t for t in trips if _delivered_today(db, t, today) and t.vehicle_id not in busy and t.driver_id]
    seen, uniq = set(), []
    for t in sorted(trucks, key=lambda t: t.id, reverse=True):  # latest delivery per vehicle
        if t.vehicle_id not in seen:
            seen.add(t.vehicle_id)
            uniq.append(t)
    with_trip = set(db.scalars(select(Trip.shipment_id).where(Trip.status.not_in(["declined", "cancelled"]))))
    jobs = [s for s in db.scalars(select(Shipment).where(Shipment.fleet_org_id == org, Shipment.status == "booked"))
            if s.id not in with_trip]
    prob = Problem([], [], [], road_distance, cfg)
    max_min = prob.max_driver_minutes
    options = []
    for t in uniq:
        v = t.vehicle
        m1 = (t.mandi.lat, t.mandi.lon)
        home = (t.origin_lat, t.origin_lon)
        used_min = float(t.planned_duration_min or 0.0)
        rate = prob.rate_per_km(Vehicle(v.id, v.capacity_tons, *home))
        for s in jobs:
            lots = [Lot(x.id, x.pickup_lat, x.pickup_lon, x.quantity_tons, x.crop) for x in s.lots]
            tons = sum(x.tons for x in lots)
            if not lots or tons > v.capacity_tons + 1e-9 or s.mandi.lat is None:
                continue
            m2 = MandiOption(s.mandi_id, s.mandi.name, s.mandi.lat, s.mandi.lon, 0, 0, 0, 30.0)
            order, path_km = order_pickups(prob, lots, m2)
            leg = lambda a, b: prob.leg(a[0], a[1], b[0], b[1])  # noqa: E731
            first, m2p = (order[0].lat, order[0].lon), (m2.lat, m2.lon)
            combined = leg(m1, first)[0] + path_km + leg(m2p, home)[0]
            separate = leg(m1, home)[0] + leg(home, first)[0] + path_km + leg(m2p, home)[0]
            saved = separate - combined
            r = prob.route(Vehicle("x", v.capacity_tons, *m1), [(m2, order)])
            day = used_min + r["minutes"] - leg(m2p, m1)[1] + leg(m2p, home)[1]
            sp = max(prob.spoilage_after(z, m2, r["clock"][z.id]) for z in order)
            if saved <= 0 or day > max_min or sp > prob.max_spoilage:
                continue
            options.append({"trip_id": t.id, "vehicle_id": v.id, "vehicle": v.registration, "driver_id": t.driver_id,
                            "from_mandi": t.mandi.name, "shipment_id": s.id, "to_mandi": s.mandi.name,
                            "tons": round(tons, 2), "truck_tons": v.capacity_tons,
                            "pickup_lot_ids": [z.id for z in order], "empty_km_saved": round(saved, 1),
                            "rs_saved": round(saved * rate), "day_hours": round(day / 60, 1),
                            "max_spoilage_pct": round(sp, 2), "is_simulated": t.is_simulated or s.is_simulated})
    chosen = []
    if options:
        model = cp_model.CpModel()
        xs = [model.NewBoolVar(f"o{i}") for i in range(len(options))]
        for key in ("trip_id", "shipment_id"):
            groups = {}
            for o, x in zip(options, xs):
                groups.setdefault(o[key], []).append(x)
            for g in groups.values():
                model.AddAtMostOne(g)
        model.Maximize(sum(o["rs_saved"] * x for o, x in zip(options, xs)))
        solver = make_solver(cfg, cfg["optimizer"]["time_limit_s"])
        if solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            chosen = [o for o, x in zip(options, xs) if solver.Value(x)]
    prop = LoadProposal(
        kind="return_load", org_id=org, created_by=user.id, est_saving=float(sum(o["rs_saved"] for o in chosen)),
        is_simulated=any(o["is_simulated"] for o in chosen), data_provenance="real",  # distances + trips, no prices
        payload={"matches": chosen, "trucks_considered": len(uniq), "jobs_considered": len(jobs),
                 "home_assumption": "a truck's home = where its delivering trip started",
                 "distances": _distances_label(prob)})
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


# ---------------------------------------------------------------- decide


def get_own(db: Session, user: User, pid: int) -> LoadProposal:
    p = db.get(LoadProposal, pid)
    if p is None or (user.role != "admin" and p.org_id != user.org_id):
        raise HTTPException(404, "Not found")  # cross-tenant: 404, not 403
    return p


def accept(db: Session, user: User, p: LoadProposal) -> dict:
    from ..routers.lots import make_shipment
    from ..routers.trips import make_trip

    if p.status != "proposed":
        raise HTTPException(409, f"Proposal is already {p.status}")
    _require_enabled(p.kind)
    created = []
    try:
        if p.kind == "consolidation":
            for L in p.payload["loads"]:
                sh = make_shipment(db, user, L["mandi_id"], L["lot_ids"], via="v3-1 shared-load proposal",
                                   proposal_id=p.id, pickup_order=L["lot_ids"])
                created.append({"shipment_id": sh.id, "lot_ids": L["lot_ids"], "mandi": L["mandi"]})
        else:
            for m in p.payload["matches"]:
                t = make_trip(db, user, m["shipment_id"], m["vehicle_id"], m["driver_id"], via="v3-1 return-load proposal",
                              proposal_id=p.id, after_trip=m["trip_id"], pickup_order=m["pickup_lot_ids"])
                created.append({"trip_id": t.id, "shipment_id": m["shipment_id"], "vehicle": m["vehicle"]})
    except HTTPException as exc:
        db.rollback()
        p = db.get(LoadProposal, p.id)
        p.status, p.decided_by, p.decided_at = "stale", user.id, datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(409, f"Proposal is out of date ({exc.detail}). Plan again.")
    p.status, p.decided_by, p.decided_at = "accepted", user.id, datetime.now(timezone.utc)
    db.add(AuditLog(entity="proposal", entity_id=p.id, field="status", from_state="proposed", to_state="accepted",
                    actor_id=user.id, details={"kind": p.kind, "created": created, "est_saving": p.est_saving}))
    db.commit()
    return {**proposal_out(p), "created": created}


def reject(db: Session, user: User, p: LoadProposal, reason: str | None) -> dict:
    if p.status != "proposed":
        raise HTTPException(409, f"Proposal is already {p.status}")
    p.status, p.decided_by, p.decided_at = "rejected", user.id, datetime.now(timezone.utc)
    p.reject_reason = (reason or "")[:200] or None
    db.add(AuditLog(entity="proposal", entity_id=p.id, field="status", from_state="proposed", to_state="rejected",
                    actor_id=user.id, details={"kind": p.kind, "reason": p.reject_reason}))
    db.commit()
    return proposal_out(p)
