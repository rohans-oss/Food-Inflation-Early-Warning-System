"""Role screens that aggregate across modules: trader, buyer, policy, lender, recommender, alerts."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..alerts import notify
from ..lifecycle import move
from ..db import get_db
from ..models import Alert, Arrival, GeofenceEvent, GpsPoint, Lot, Mandi, Price, Shipment, Trip, User
from ..rbac import forbid, get_current_user, require
from ..scoping import scoped_lots
from ..supply import in_transit, ist_today, recommend, supply_board, typical_daily_arrivals
from .lots import get_scoped_lot
from .prices import forecast_block

router = APIRouter(tags=["role views"])


# ------------------------------------------------------------------ farmer / FPO: best mandi


@router.get("/recommend/best-mandi")
def best_mandi(
    lot_id: int | None = None,
    lat: float | None = None,
    lon: float | None = None,
    tons: float | None = None,
    weeks: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require("recommend:read")),
):
    if not 1 <= weeks <= 4:
        raise HTTPException(400, "weeks must be 1-4")
    if lot_id is not None:
        lot = get_scoped_lot(db, user, lot_id)
        lat, lon, tons = lot.pickup_lat, lot.pickup_lon, lot.quantity_tons
    if lat is None or lon is None or not tons:
        raise HTTPException(400, "Give lot_id, or lat + lon + tons")
    from ..decisions.service import recommend_single  # V3-0: rule or optimizer, per config/recommender.toml

    return recommend_single(db, lat, lon, tons, weeks=weeks)


# ------------------------------------------------------------------ in-transit supply


@router.get("/supply/in-transit")
def in_transit_view(mandi_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require("intransit:read"))):
    ids = [mandi_id] if mandi_id else None
    if user.role == "trader":
        ids = [user.mandi_id]
    elif user.role == "buyer" and not mandi_id:
        ids = user.watch_mandi_ids or []
    rows = supply_board(db, ids)
    if user.role in ("buyer", "policy"):
        for r in rows:  # aggregates only: no individual vehicles for these roles
            r["vehicles"] = []
    return rows


# ------------------------------------------------------------------ trader


@router.get("/trader/board")
def trader_board(db: Session = Depends(get_db), user: User = Depends(require("arrivals:confirm"))):
    if not user.mandi_id:
        raise HTTPException(400, "Your account is not linked to a mandi")
    mandi = db.get(Mandi, user.mandi_id)
    incoming = db.scalars(select(Trip).where(Trip.mandi_id == mandi.id, Trip.status.in_(["accepted", "in_progress"]))
                          .order_by(Trip.eta_at)).all()
    today = ist_today()
    delivered_today = db.scalars(select(Lot).join(Shipment, Lot.shipment_id == Shipment.id).where(
        Shipment.mandi_id == mandi.id, Lot.status == "delivered", Lot.delivered_at >= datetime.now(timezone.utc) - timedelta(hours=24))).all()
    at_gate = db.scalars(select(Lot).join(Shipment, Lot.shipment_id == Shipment.id).where(
        Shipment.mandi_id == mandi.id, Lot.status == "at_mandi")).all()
    board = supply_board(db, [mandi.id])[0]
    return {
        "mandi": {"id": mandi.id, "name": mandi.name},
        "summary": {k: board[k] for k in ("tons_in_transit", "tons_real", "tons_simulated", "expected_today_tons_total",
                                          "confirmed_today_tons", "typical_daily_tons", "typical_is_synthetic", "typical_provenance",
                                          "expected_vs_normal", "note")},
        "incoming": [{"trip_id": t.id, "vehicle": t.vehicle.registration, "tons": t.load_tons, "status": t.status,
                      "eta_at": t.eta_at, "remaining_km": t.remaining_km, "is_simulated": t.is_simulated,
                      "lat": t.last_lat, "lon": t.last_lon, "pickup_scanned": t.pickup_scanned_at is not None}
                     for t in incoming],
        "awaiting_weighing": [{"lot_id": lot.id, "farmer": lot.farmer.full_name, "declared_tons": lot.quantity_tons,
                               "grade": lot.grade, "shipment_id": lot.shipment_id} for lot in at_gate],
        "delivered_last_24h": [{"lot_id": lot.id, "farmer": lot.farmer.full_name, "kg": lot.delivered_weight_kg,
                                "price_per_quintal": lot.sale_price_per_quintal} for lot in delivered_today],
        "date": today,
    }


class WeighIn(BaseModel):
    weight_kg: float = Field(gt=0, le=60000)
    price_per_quintal: float = Field(gt=0, le=50000)


@router.post("/trader/lots/{lot_id}/weigh")
def weigh_lot(lot_id: int, body: WeighIn, db: Session = Depends(get_db), user: User = Depends(require("arrivals:confirm"))):
    lot = get_scoped_lot(db, user, lot_id)
    if lot.status != "at_mandi":
        raise HTTPException(409, "Scan the vehicle's delivery QR first; the lot must be at the mandi")
    now = datetime.now(timezone.utc)
    lot.delivered_weight_kg, lot.sale_price_per_quintal = body.weight_kg, body.price_per_quintal
    move(db, lot, "delivered", user.id, weight_kg=body.weight_kg, price_per_quintal=body.price_per_quintal)
    lot.delivered_at = now
    sh = lot.shipment
    # Confirmed tonnage feeds the mandi's arrivals series (source kept separate from Agmarknet)
    day = ist_today(now)
    a = db.scalar(select(Arrival).where(Arrival.mandi_id == sh.mandi_id, Arrival.commodity == lot.crop,
                                        Arrival.date == day, Arrival.source == "trader_confirmed"))
    if a is None:
        db.add(Arrival(mandi_id=sh.mandi_id, commodity=lot.crop, date=day, tonnes=body.weight_kg / 1000, source="trader_confirmed"))
    else:
        a.tonnes += body.weight_kg / 1000
    notify(db, lot.farmer, "delivered", f"delivered:{lot.id}", lot=lot.id, mandi=sh.mandi.name,
           kg=round(body.weight_kg), price=round(body.price_per_quintal), payout=lot.payout_status)
    if all(x.status == "delivered" for x in sh.lots) and sh.status == "in_transit":
        move(db, sh, "delivered", user.id)
    db.commit()
    return {"lot_id": lot.id, "status": lot.status, "delivered_weight_kg": lot.delivered_weight_kg,
            "sale_price_per_quintal": lot.sale_price_per_quintal, "shipment_status": sh.status}


# ------------------------------------------------------------------ bulk buyer


@router.get("/buyer/overview")
def buyer_overview(db: Session = Depends(get_db), user: User = Depends(require("forecasts:read"))):
    if user.role not in ("buyer", "admin"):
        raise HTTPException(403, "Buyer view")
    ids = user.watch_mandi_ids or []
    supply = {r["mandi_id"]: r for r in supply_board(db, ids)}
    out = []
    for mid in ids:
        m = db.get(Mandi, mid)
        if m is None:
            continue
        s = supply.get(mid, {})
        out.append({"mandi": {"id": m.id, "name": m.name, "district": m.district},
                    "forecast": forecast_block(db, mid),
                    "expected_tons_next_3_days": s.get("expected_3d_tons"),
                    "tons_in_transit": s.get("tons_in_transit"),
                    "typical_daily_tons": s.get("typical_daily_tons")})
    return {"watching": out, "hint": "Change watched mandis with PATCH /auth/me {watch_mandi_ids: [...]}"}


# ------------------------------------------------------------------ policy


@router.get("/policy/overview")
def policy_overview(state: str | None = None, db: Session = Depends(get_db), _=Depends(require("policy:read"))):
    today = date.today()
    moving = in_transit(db)
    by_district: dict[tuple[str, str], dict] = {}
    mandis = []
    for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None)).order_by(Mandi.name)):
        if state and m.state != state:
            continue
        rows = db.execute(select(Price.date, func.avg(Price.modal_price)).where(
            Price.mandi_id == m.id, Price.is_outlier.is_(False), Price.date >= today - timedelta(days=60))
            .group_by(Price.date).order_by(Price.date)).all()
        latest = rows[-1][1] if rows else None
        four_wk = next((p for d, p in reversed(rows) if d <= rows[-1][0] - timedelta(days=28)), None) if rows else None
        trend = round((latest / four_wk - 1) * 100, 1) if latest and four_wk else None
        fc = forecast_block(db, m.id)
        typ = typical_daily_arrivals(db, m.id)
        # last 7 complete days (today's arrivals are still coming in), mean over reporting days
        tot, ndays = db.execute(select(func.sum(Arrival.tonnes), func.count(func.distinct(Arrival.date))).where(
            Arrival.mandi_id == m.id, Arrival.date >= today - timedelta(days=7), Arrival.date < today,
            (Arrival.source == "synthetic") if typ["is_synthetic"] else (Arrival.source != "synthetic"))).one()
        anomaly = round((tot / ndays) / typ["tons"] - 1, 2) if tot and ndays and typ["tons"] else None
        it = moving.get(m.id, {}).get("tons_in_transit", 0.0)
        row = {"mandi_id": m.id, "mandi": m.name, "district": m.district, "state": m.state, "lat": m.lat, "lon": m.lon,
               "latest_price": round(latest) if latest else None, "price_date": rows[-1][0] if rows else None,
               "trend_4w_pct": trend, "spike_prob_14d": fc["spike_prob_14d"] if fc else None,
               "forecast_2w": next((h for h in fc["horizons"] if h["weeks"] == 2), None) if fc else None,
               "trained_on_synthetic": fc["trained_on_synthetic"] if fc else None,
               "data_provenance": fc["data_provenance"] if fc else None,
               "tons_in_transit": it, "arrival_anomaly_7d": anomaly, "arrivals_synthetic": typ["is_synthetic"]}
        mandis.append(row)
        d = by_district.setdefault((m.state, m.district), {"state": m.state, "district": m.district, "mandis": 0,
                                                            "max_spike_prob": None, "tons_in_transit": 0.0, "prices": [], "trends": []})
        d["mandis"] += 1
        d["tons_in_transit"] = round(d["tons_in_transit"] + it, 2)
        if row["spike_prob_14d"] is not None:
            d["max_spike_prob"] = max(d["max_spike_prob"] or 0, row["spike_prob_14d"])
        if latest:
            d["prices"].append(latest)
        if trend is not None:
            d["trends"].append(trend)
    districts = []
    for d in by_district.values():
        prices, trends = d.pop("prices"), d.pop("trends")
        d["avg_price"] = round(sum(prices) / len(prices)) if prices else None
        d["avg_trend_4w_pct"] = round(sum(trends) / len(trends), 1) if trends else None
        districts.append(d)
    districts.sort(key=lambda d: -(d["max_spike_prob"] or 0))
    return {"mandis": mandis, "districts": districts,
            "notes": ["Arrival anomaly = mean daily arrivals over the last 7 complete days vs the 28-day typical (-0.3 = 30% below normal).",
                      "Tons in transit counts tracked vehicles only.", "Scenario simulator arrives in V3."]}


# ------------------------------------------------------------------ lender / insurer


def trip_reliability(db: Session, t: Trip) -> dict:
    """Simple, explainable 0-100 score: both QR scans, GPS coverage, on time, no unexplained stops."""
    pts = db.scalars(select(GpsPoint.recorded_at).where(GpsPoint.trip_id == t.id).order_by(GpsPoint.recorded_at)).all()
    events = [e.event for e in db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id))]
    coverage = None
    if t.started_at and t.ended_at and pts:
        minutes = max((t.ended_at - t.started_at).total_seconds() / 60, 1)
        covered = len({(p.replace(tzinfo=timezone.utc) if p.tzinfo is None else p).replace(second=0, microsecond=0) for p in pts})
        coverage = min(1.0, covered / minutes)
    on_time = None
    arrived = next((e for e in db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id,
                                                                       GeofenceEvent.event == "reached_mandi"))), None)
    if arrived and t.started_at and t.planned_duration_min:
        on_time = arrived.occurred_at <= t.started_at + timedelta(minutes=t.planned_duration_min + 45)
    checks = {
        "pickup_qr": t.pickup_scanned_at is not None,
        "delivery_qr": t.delivery_scanned_at is not None,
        "gps_coverage_ge_80pct": coverage is not None and coverage >= 0.8,
        "on_time": bool(on_time),
        "no_unexpected_stop": "unexpected_stop" not in events,
    }
    weights = {"pickup_qr": 25, "delivery_qr": 25, "gps_coverage_ge_80pct": 20, "on_time": 15, "no_unexpected_stop": 15}
    return {"score": sum(w for k, w in weights.items() if checks[k]), "checks": checks,
            "gps_coverage": round(coverage, 2) if coverage is not None else None, "events": events}


@router.get("/lender/lots")
def lender_lots(db: Session = Depends(get_db), user: User = Depends(require("lender:read"))):
    lots = db.scalars(scoped_lots(user).order_by(Lot.created_at.desc())).all()
    out, farmers = [], {}
    for lot in lots:
        trip = db.scalar(select(Trip).where(Trip.shipment_id == lot.shipment_id, Trip.status == "completed")) if lot.shipment_id else None
        rel = trip_reliability(db, trip) if trip else None
        out.append({
            "lot_id": lot.id, "farmer_id": lot.farmer_id, "farmer": lot.farmer.full_name, "crop": lot.crop,
            "declared_tons": lot.quantity_tons, "status": lot.status,
            "pickup": {"label": lot.pickup_label, "scanned_at": trip.pickup_scanned_at if trip else None},
            "route": {"planned_km": trip.planned_distance_km, "started_at": trip.started_at, "ended_at": trip.ended_at,
                      "vehicle": trip.vehicle.registration, "is_simulated": trip.is_simulated} if trip else None,
            "delivery": {"mandi": lot.shipment.mandi.name if lot.shipment else None,
                         "scanned_at": trip.delivery_scanned_at if trip else None,
                         "weight_kg": lot.delivered_weight_kg, "price_per_quintal": lot.sale_price_per_quintal,
                         "value_rs": round(lot.delivered_weight_kg / 100 * lot.sale_price_per_quintal)
                         if lot.delivered_weight_kg and lot.sale_price_per_quintal else None},
            "reliability": rel,
        })
        f = farmers.setdefault(lot.farmer_id, {"farmer_id": lot.farmer_id, "farmer": lot.farmer.full_name, "lots": 0,
                                               "delivered": 0, "scores": []})
        f["lots"] += 1
        f["delivered"] += lot.status == "delivered"
        if rel:
            f["scores"].append(rel["score"])
    summary = []
    for f in farmers.values():
        sc = f.pop("scores")
        f["avg_trip_reliability"] = round(sum(sc) / len(sc)) if sc else None
        summary.append(f)
    return {"lots": out, "farmers": summary,
            "note": "Only lots whose farmer chose to share them with your organization are listed."}


# ------------------------------------------------------------------ alerts inbox


@router.get("/alerts")
def my_alerts(unread: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(100)
    if unread:
        q = q.where(Alert.read_at.is_(None))
    return [{"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "body": a.body, "lang": a.lang,
             "created_at": a.created_at, "read_at": a.read_at, "channels": a.channels} for a in db.scalars(q)]


@router.post("/alerts/{alert_id}/read")
def read_alert(alert_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Alert, alert_id)
    if a is None or a.user_id != user.id:
        raise forbid()
    a.read_at = a.read_at or datetime.now(timezone.utc)
    db.commit()
    return {"id": a.id, "read_at": a.read_at}


# ------------------------------------------------------------------ fleet owner


@router.get("/fleet/overview")
def fleet_overview(db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    from ..models import Vehicle

    org = user.org_id
    vq = select(Vehicle) if user.role == "admin" else select(Vehicle).where(Vehicle.org_id == org)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    vehicles = []
    for v in db.scalars(vq.order_by(Vehicle.registration)):
        trips = db.scalars(select(Trip).where(Trip.vehicle_id == v.id).order_by(Trip.created_at.desc())).all()
        active = next((t for t in trips if t.status in ("accepted", "in_progress")), None)
        recent = [t for t in trips if t.started_at and t.started_at >= since]
        hours = sum(((t.ended_at or datetime.now(timezone.utc)) - t.started_at).total_seconds() / 3600 for t in recent)
        vehicles.append({
            "id": v.id, "registration": v.registration, "capacity_tons": v.capacity_tons, "is_simulated": v.is_simulated,
            "active_trip": {"id": active.id, "status": active.status, "mandi": active.mandi.name, "lat": active.last_lat,
                            "lon": active.last_lon, "eta_at": active.eta_at, "load_tons": active.load_tons,
                            "remaining_km": active.remaining_km} if active else None,
            "utilization_30d": {"trips": len(recent), "hours_on_trip": round(hours, 1),
                                "pct_of_time": round(100 * hours / (30 * 24), 1),
                                "avg_load_pct": round(100 * sum(t.load_tons for t in recent) / (len(recent) * v.capacity_tons), 1) if recent else None},
            "history": [{"trip_id": t.id, "status": t.status, "mandi": t.mandi.name, "started_at": t.started_at,
                         "ended_at": t.ended_at, "load_tons": t.load_tons, "km": t.planned_distance_km} for t in trips[:10]],
        })
    pending = db.scalars(select(Shipment).where(Shipment.fleet_org_id == org, Shipment.status == "booked")).all() if org else []
    return {"vehicles": vehicles,
            "bookings_to_assign": [{"shipment_id": s.id, "mandi": s.mandi.name, "tons": round(sum(lot.quantity_tons for lot in s.lots), 2),
                                    "booked_at": s.booked_at} for s in pending
                                   if not db.scalar(select(Trip.id).where(Trip.shipment_id == s.id, Trip.status.not_in(["declined", "cancelled"])))]}


@router.get("/module-status")
def module_status(db: Session = Depends(get_db), _=Depends(require("policy:read"))):
    """V3-3 rule 23 (Policy + Admin): each module's data status - real / real_partial / synthetic /
    not_yet_evaluable - with the evidence, per mandi where it applies."""
    from ..module_status import compute

    return compute(db)
