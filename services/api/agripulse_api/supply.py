"""In-transit supply (display only in V1) and the rule-based best-mandi recommender."""
from datetime import date, datetime, timedelta, timezone
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tracking.geo import haversine_km
from tracking.routing import road_km

from .config import get_settings
from .models import Arrival, Forecast, Mandi, Price, Trip, Weather

IST = timezone(timedelta(hours=5, minutes=30))
CROP_SENSITIVITY = {"Tomato": 1.0}  # V1 crop; onion ~0.3, pulses ~0.05 when added


def ist_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(IST).date()


def end_of_ist_day(now: datetime | None = None) -> datetime:
    d = ist_today(now)
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=IST)


# ------------------------------------------------------------------ in-transit supply


def in_transit(db: Session, now: datetime | None = None) -> dict[int, dict]:
    now = now or datetime.now(timezone.utc)
    eod = end_of_ist_day(now)
    out: dict[int, dict] = {}
    q = select(Trip).where(Trip.status.in_(["accepted", "in_progress"]))
    for t in db.scalars(q):
        m = out.setdefault(t.mandi_id, {"tons_in_transit": 0.0, "tons_real": 0.0, "tons_simulated": 0.0, "trucks": 0,
                                         "trucks_moving": 0, "expected_today_tons": 0.0, "expected_3d_tons": 0.0,
                                         "vehicles": []})
        moving = t.status == "in_progress"
        if moving:
            m["tons_in_transit"] += t.load_tons
            m["tons_simulated" if t.is_simulated else "tons_real"] += t.load_tons
            m["trucks_moving"] += 1
        m["trucks"] += 1
        eta = t.eta_at
        if moving and eta and eta <= eod:
            m["expected_today_tons"] += t.load_tons
        if eta and eta <= now + timedelta(days=3) or not moving:
            m["expected_3d_tons"] += t.load_tons
        m["vehicles"].append({"trip_id": t.id, "vehicle": t.vehicle.registration, "tons": t.load_tons, "status": t.status,
                              "eta_at": eta, "remaining_km": t.remaining_km, "is_simulated": t.is_simulated,
                              "lat": t.last_lat, "lon": t.last_lon})
    for m in out.values():
        for k in ("tons_in_transit", "tons_real", "tons_simulated", "expected_today_tons", "expected_3d_tons"):
            m[k] = round(m[k], 2)
    return out


def typical_daily_arrivals(db: Session, mandi_id: int, commodity: str = "Tomato", days: int = 28) -> dict:
    """Median daily tonnes over the last `days` days that had any arrivals. Real sources first."""
    since = date.today() - timedelta(days=days)
    for synthetic in (False, True):
        rows = db.execute(
            select(Arrival.date, func.sum(Arrival.tonnes))
            .where(Arrival.mandi_id == mandi_id, Arrival.commodity == commodity, Arrival.date >= since,
                   (Arrival.source == "synthetic") if synthetic else (Arrival.source != "synthetic"))
            .group_by(Arrival.date)
        ).all()
        if len(rows) >= 5:
            return {"tons": round(median(r[1] for r in rows), 1), "days": len(rows), "is_synthetic": synthetic}
    return {"tons": None, "days": 0, "is_synthetic": False}


def supply_board(db: Session, mandi_ids: list[int] | None = None, now: datetime | None = None) -> list[dict]:
    """'Arrivals expected today vs normal' per mandi."""
    now = now or datetime.now(timezone.utc)
    moving = in_transit(db, now)
    today = ist_today(now)
    q = select(Mandi).where(Mandi.lat.is_not(None)).order_by(Mandi.name)
    if mandi_ids is not None:
        q = q.where(Mandi.id.in_(mandi_ids))
    out = []
    for m in db.scalars(q):
        it = moving.get(m.id, {"tons_in_transit": 0.0, "tons_real": 0.0, "tons_simulated": 0.0, "trucks": 0,
                               "trucks_moving": 0, "expected_today_tons": 0.0, "expected_3d_tons": 0.0, "vehicles": []})
        confirmed = db.scalar(select(func.sum(Arrival.tonnes)).where(
            Arrival.mandi_id == m.id, Arrival.date == today, Arrival.source == "trader_confirmed")) or 0.0
        typical = typical_daily_arrivals(db, m.id)
        expected = round(confirmed + it["expected_today_tons"], 2)
        ratio = round(expected / typical["tons"], 2) if typical["tons"] else None
        out.append({"mandi_id": m.id, "mandi": m.name, "district": m.district, "state": m.state, "lat": m.lat, "lon": m.lon,
                    **it, "confirmed_today_tons": round(confirmed, 2), "expected_today_tons_total": expected,
                    "typical_daily_tons": typical["tons"], "typical_is_synthetic": typical["is_synthetic"],
                    "expected_vs_normal": ratio,
                    "note": "Tracked vehicles only - most arrivals are not tracked in V1, so this is a lower bound."})
    return out


# ------------------------------------------------------------------ best-mandi recommender


def _latest_forecast(db: Session, mandi_id: int, weeks: int) -> Forecast | None:
    return db.scalar(select(Forecast).where(Forecast.mandi_id == mandi_id, Forecast.horizon_weeks == weeks)
                     .order_by(Forecast.issue_date.desc()).limit(1))


def _temp_c(db: Session, mandi_id: int) -> tuple[float, str]:
    w = db.scalar(select(Weather).where(Weather.mandi_id == mandi_id, Weather.date == date.today(),
                                        Weather.tmax_c.is_not(None)).limit(1))
    return (w.tmax_c, w.source) if w else (30.0, "default")


def spoilage_pct(hours: float, temp_c: float, crop: str = "Tomato") -> float:
    """% of value lost in transit: base rate at 30C, doubling every +10C (Q10 = 2)."""
    s = get_settings()
    return s.spoilage_pct_per_hour_at_30c * hours * 2 ** ((temp_c - 30) / 10) * CROP_SENSITIVITY.get(crop, 1.0)


def recommend(db: Session, lat: float, lon: float, tons: float, crop: str = "Tomato", weeks: int = 1,
              radius_km: float = 300, max_candidates: int = 12) -> dict:
    s = get_settings()
    cands = []
    for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None))):
        d = haversine_km(lat, lon, m.lat, m.lon)
        if d <= radius_km:
            cands.append((d, m))
    cands.sort(key=lambda x: x[0])
    ranked, no_forecast = [], []
    for _, m in cands[:max_candidates]:
        f = _latest_forecast(db, m.id, weeks)
        if f is None:
            no_forecast.append(m.name)
            continue
        km, minutes, route_src = road_km(lat, lon, m.lat, m.lon)
        hours = minutes / 60
        temp, temp_src = _temp_c(db, m.id)
        spoil = spoilage_pct(hours, temp, crop)
        transport = km * s.transport_rate_per_km_ton * tons
        quintals = tons * 10

        def net(price):
            gross = price * quintals
            return round(gross - transport - gross * spoil / 100)

        latest = db.scalar(select(Price.modal_price).where(Price.mandi_id == m.id, Price.is_outlier.is_(False))
                           .order_by(Price.date.desc()).limit(1))
        ranked.append({
            "mandi_id": m.id, "mandi": m.name, "district": m.district, "state": m.state,
            "coords_verified": m.coords_verified,
            "road_km": km, "drive_hours": round(hours, 1), "route_source": route_src,
            "price_forecast": {"p10": f.p10, "p50": f.p50, "p90": f.p90, "weeks": weeks, "issue_date": f.issue_date},
            "latest_price": latest, "spike_prob_14d": f.spike_prob,
            "transport_cost": round(transport), "spoilage_pct": round(spoil, 2), "temp_c": temp, "temp_source": temp_src,
            "net_value": {"p10": net(f.p10), "p50": net(f.p50), "p90": net(f.p90)},
            "trained_on_synthetic": f.trained_on_synthetic,
        })
    ranked.sort(key=lambda r: r["net_value"]["p50"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        # overlapping ranges mean the ranking is not decisive
        r["clearly_better_than_next"] = i + 1 < len(ranked) and r["net_value"]["p10"] > ranked[i + 1]["net_value"]["p90"]
    return {
        "inputs": {"lat": lat, "lon": lon, "tons": tons, "crop": crop, "weeks": weeks,
                   "rate_per_km_ton": s.transport_rate_per_km_ton, "spoilage_pct_per_hour_at_30c": s.spoilage_pct_per_hour_at_30c},
        "formula": "net = p50 x quantity - distance x rate x tons - spoilage% x gross (spoilage% = base x hours x 2^((T-30)/10))",
        "ranked": ranked,
        "no_forecast": no_forecast,
    }
