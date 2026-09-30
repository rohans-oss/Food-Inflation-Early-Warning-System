"""In-transit supply (display only in V1) and the rule-based best-mandi recommender."""
import os
import tomllib
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tracking.geo import haversine_km
from tracking.routing import road_km

from .modelcfg import display_model
from .models import Arrival, Forecast, Mandi, Price, Trip, Weather

IST = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "recommender.toml"


@lru_cache
def cost_config() -> dict:
    """All recommender cost parameters live in config/recommender.toml (brief: 'keep them in a config file')."""
    path = Path(os.environ.get("RECOMMENDER_CONFIG", _DEFAULT_CONFIG))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


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
                    "typical_provenance": "synthetic" if typical["is_synthetic"] else "real",
                    "expected_vs_normal": ratio,
                    "note": "Tracked vehicles only - most arrivals are not tracked in V1, so this is a lower bound."})
    return out


# ------------------------------------------------------------------ best-mandi recommender


def _latest_forecast(db: Session, mandi_id: int, weeks: int) -> Forecast | None:
    return db.scalar(select(Forecast).where(Forecast.mandi_id == mandi_id, Forecast.horizon_weeks == weeks,
                                            Forecast.model_name == display_model())
                     .order_by(Forecast.issue_date.desc()).limit(1))


def _temp_c(db: Session, mandi_id: int) -> tuple[float, str]:
    w = db.scalar(select(Weather).where(Weather.mandi_id == mandi_id, Weather.date == date.today(),
                                        Weather.tmax_c.is_not(None)).limit(1))
    return (w.tmax_c, w.source) if w else (cost_config()["spoilage"]["default_temp_c"], "default")


def spoilage_pct(hours: float, temp_c: float, crop: str = "Tomato") -> float:
    """% of value lost in transit: base rate at 30C, doubling every +10C (Q10 = 2)."""
    sp = cost_config()["spoilage"]
    sens = sp["crop_sensitivity"].get(crop, 1.0)
    return sp["base_pct_per_hour"] * hours * sp["q10"] ** ((temp_c - sp["reference_temp_c"]) / 10) * sens


def recommend(db: Session, lat: float, lon: float, tons: float, crop: str = "Tomato", weeks: int = 1,
              radius_km: float | None = None, max_candidates: int | None = None) -> dict:
    cfg = cost_config()
    rate = cfg["transport"]["rate_per_km_ton"]
    radius_km = radius_km or cfg["search"]["radius_km"]
    max_candidates = max_candidates or cfg["search"]["max_candidates"]
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
        transport = km * rate * tons
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
            "data_provenance": f.data_provenance,
        })
    ranked.sort(key=lambda r: r["net_value"]["p50"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        # overlapping ranges mean the ranking is not decisive
        r["clearly_better_than_next"] = i + 1 < len(ranked) and r["net_value"]["p10"] > ranked[i + 1]["net_value"]["p90"]
    return {
        "inputs": {"lat": lat, "lon": lon, "tons": tons, "crop": crop, "weeks": weeks,
                   "rate_per_km_ton": rate, "spoilage": {k: v for k, v in cfg["spoilage"].items()},
                   "config_file": Path(cfg["_path"]).name},
        "formula": "net = price x quantity - road km x rate x tons - spoilage% x gross; spoilage% = base x hours x q10^((T-Tref)/10) x crop sensitivity",
        "ranked": ranked,
        "no_forecast": no_forecast,
    }


PRICE_FRESH_DAYS = 14  # a mandi's latest real price older than this is not shown as "today's price"


def latest_crop_prices(db: Session, crop: str, fresh_days: int = PRICE_FRESH_DAYS) -> dict[int, dict]:
    """{mandi_id: latest price} for any crop (config or farmer-added) from stored Agmarknet rows: median modal across
    varieties on the mandi's latest non-outlier day within `fresh_days`. Rs per quintal."""
    from .crops import feed_name

    fn = feed_name(db, crop)
    if not fn:
        return {}
    since = date.today() - timedelta(days=fresh_days)
    cond = [func.lower(Price.commodity) == fn.lower(), Price.is_outlier.is_(False), Price.date >= since]
    last = select(Price.mandi_id, func.max(Price.date).label("d")).where(*cond).group_by(Price.mandi_id).subquery()
    rows = db.scalars(select(Price).join(last, (Price.mandi_id == last.c.mandi_id) & (Price.date == last.c.d))
                      .where(*cond)).all()
    out: dict[int, dict] = {}
    for p in rows:
        out.setdefault(p.mandi_id, {"rows": []})["rows"].append(p)
    for mid, v in out.items():
        rs = v.pop("rows")
        synthetic = any(r.source == "synthetic" for r in rs)
        v.update({"modal": round(median(r.modal_price for r in rs)), "date": rs[0].date,
                  "min": min((r.min_price for r in rs if r.min_price is not None), default=None),
                  "max": max((r.max_price for r in rs if r.max_price is not None), default=None),
                  "data_provenance": "synthetic" if synthetic else "real", "source": "Agmarknet" if not synthetic else "synthetic"})
    return out


def forecast_available(db: Session, crop: str) -> bool:
    """A crop gets the forecast-based recommender only when it has a model AND recent display-model forecasts exist
    (in LIVE mode tomato has none until real history passes the readiness threshold)."""
    from .crops import has_forecast

    if not has_forecast(crop):
        return False
    return db.scalar(select(func.count()).select_from(Forecast).where(
        Forecast.model_name == display_model(), Forecast.commodity == crop,
        Forecast.issue_date >= date.today() - timedelta(days=7))) > 0


def options_without_forecast(db: Session, lat: float, lon: float, tons: float, crop: str) -> dict:
    """Mandi options when there is NO price forecast for the crop (every crop but tomato, and tomato in LIVE mode until
    real history is long enough): nearest mandis with road distance, a hired-truck transport cost, crop-specific
    spoilage and the latest REAL Agmarknet price where the mandi reported one. Value at today's price is not a
    forecast; mandis with a price rank by it, the rest by transport cost."""
    cfg = cost_config()
    t = cfg["transport"]["vehicle"]
    sizes = sorted(t.get("sizes_tons", [2.5, 5, 9, 10, 16]))
    cap = next((s for s in sizes if s >= tons - 1e-9), sizes[-1])
    rate = t["base_rate_per_km"] + t["rate_per_km_per_capacity_ton"] * cap
    legs = 2 if t.get("count_return_leg", True) else 1
    radius = cfg["search"]["radius_km"]
    cands = sorted(((haversine_km(lat, lon, m.lat, m.lon), m) for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None)))),
                   key=lambda x: x[0])
    prices = latest_crop_prices(db, crop)
    rows = []
    for _, m in [c for c in cands if c[0] <= radius][: cfg["search"]["max_candidates"]]:
        km, minutes, src = road_km(lat, lon, m.lat, m.lon)
        temp, temp_src = _temp_c(db, m.id)
        sp = spoilage_pct(minutes / 60, temp, crop)
        cost = round(km * legs * rate)
        pr = prices.get(m.id)
        value = round(pr["modal"] * tons * 10 * (1 - sp / 100) - cost) if pr else None  # 10 quintal per tonne
        rows.append({"mandi_id": m.id, "mandi": m.name, "district": m.district, "state": m.state,
                     "coords_verified": m.coords_verified, "road_km": km, "drive_hours": round(minutes / 60, 1),
                     "route_source": src, "price_forecast": None, "net_value": None, "spike_prob_14d": None,
                     "price_today": ({"modal": pr["modal"], "min": pr["min"], "max": pr["max"], "date": pr["date"],
                                      "source": pr["source"]} if pr else None),
                     "value_at_today_price": value,
                     "transport_cost": cost, "spoilage_pct": round(sp, 2),
                     "temp_c": temp, "temp_source": temp_src, "data_provenance": pr["data_provenance"] if pr else None,
                     "feasible": True})
    rows.sort(key=lambda r: (r["value_at_today_price"] is None, -(r["value_at_today_price"] or 0), r["transport_cost"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    from .crops import has_forecast

    why = (f"{crop} forecasts need at least a year of real price history, which is still being collected"
           if has_forecast(crop) else f"AgriPulse has no price model for {crop}")
    priced = sum(1 for r in rows if r["price_today"])
    return {"crop": crop, "no_price_forecast": True, "recommender": "today_price" if priced else "distance",
            "why_no_forecast": why, "priced_mandis": priced,
            "formula": (f"No forecast ({why}). Mandis with a recent real Agmarknet price rank by value at that price "
                        f"(price x quantity - spoilage - transport); the rest by transport cost (hired {cap:g} t truck, "
                        f"Rs {rate:.0f}/km{', both ways' if legs == 2 else ''}). Today's price is not a prediction"),
            "ranked": rows, "no_forecast": [], "inputs": {"config_file": Path(cfg["_path"]).name}}
