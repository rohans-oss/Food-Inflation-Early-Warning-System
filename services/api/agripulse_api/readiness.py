"""Real-data readiness monitor (V2-0).

How much REAL history each mandi has per data type, whether it crosses the threshold in
config/readiness.toml, and when it will if collection continues. Synthetic rows never count.
"""
import os
import tomllib
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Arrival, Mandi, Price, Trip, Weather
from .provenance import REAL, REAL_PARTIAL, SYNTHETIC

_DEFAULT = Path(__file__).resolve().parents[3] / "config" / "readiness.toml"
GROUPS = ("prices", "weather", "weather_forecasts", "arrivals", "transit")


@lru_cache
def readiness_config() -> dict:
    path = Path(os.environ.get("READINESS_CONFIG", _DEFAULT))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def _series_stats(first: date | None, last: date | None, days_with_data: int, cfg: dict, stale_after: int, today: date) -> dict:
    need, max_missing = cfg["min_real_days"], cfg["max_missing_pct"]
    if first is None:
        return {"first_date": None, "last_date": None, "history_days": 0, "days_with_data": 0, "missing_pct": None,
                "ready": False, "status": "no_data", "projected_ready_date": None, "days_to_go": need}
    span = (last - first).days + 1
    missing = round(100 * (1 - days_with_data / span), 1)
    stale = (today - last).days > stale_after
    ready = span >= need and missing <= max_missing
    if ready:
        status, projected = "ready", None
    elif stale:
        status, projected = "stalled", None  # collection stopped: no honest projection
    elif span >= need:
        status, projected = "too_many_gaps", None  # long enough, but too patchy; more days won't fix old gaps quickly
    else:
        status, projected = "collecting", first + timedelta(days=need - 1)
    return {"first_date": first, "last_date": last, "history_days": span, "days_with_data": days_with_data,
            "missing_pct": missing, "ready": ready, "status": status, "projected_ready_date": projected,
            "days_to_go": max(0, need - span)}


def _date_stats(db: Session, model, where) -> dict[int, tuple]:
    rows = db.execute(
        select(model.mandi_id, func.min(model.date), func.max(model.date), func.count(func.distinct(model.date)))
        .where(*where).group_by(model.mandi_id)
    ).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _issue_stats(db: Session) -> dict[int, tuple]:
    """Archived forecasts count by the day they were ISSUED (V2-1 weather_forecasts table)."""
    from .models import WeatherForecast

    rows = db.execute(
        select(WeatherForecast.mandi_id, func.min(WeatherForecast.issued_on), func.max(WeatherForecast.issued_on),
               func.count(func.distinct(WeatherForecast.issued_on))).group_by(WeatherForecast.mandi_id)
    ).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def compute(db: Session, today: date | None = None, commodity: str = "Tomato") -> dict:
    cfg = readiness_config()
    today = today or date.today()
    stale_after = cfg["general"]["stale_after_days"]
    stats = {
        "prices": _date_stats(db, Price, [Price.commodity == commodity, Price.source != "synthetic", Price.is_outlier.is_(False)]),
        "weather": _date_stats(db, Weather, [Weather.source != "synthetic", Weather.is_forecast.is_(False)]),
        "arrivals": _date_stats(db, Arrival, [Arrival.commodity == commodity, Arrival.source != "synthetic"]),
        "weather_forecasts": _issue_stats(db),
    }
    trips = db.execute(
        select(Trip.mandi_id, func.count(Trip.id), func.min(Trip.started_at), func.max(Trip.started_at))
        .where(Trip.is_simulated.is_(False), Trip.status == "completed").group_by(Trip.mandi_id)
    ).all()
    trip_stats = {r[0]: r[1:] for r in trips}
    tcfg = cfg["transit"]

    mandis, summary = [], {g: {"ready": 0, "total": 0, "latest_projected_ready_date": None, "no_data": 0} for g in GROUPS}
    for m in db.scalars(select(Mandi).order_by(Mandi.name)):
        row = {"mandi_id": m.id, "mandi": m.name, "district": m.district, "state": m.state}
        for g in ("prices", "weather", "weather_forecasts", "arrivals"):
            first, last, n = stats[g].get(m.id, (None, None, 0))
            row[g] = _series_stats(first, last, n, cfg[g], stale_after, today)
        n_trips, t0, t1 = trip_stats.get(m.id, (0, None, None))
        covered = ((t1.date() - t0.date()).days + 1) if t0 else 0
        row["transit"] = {"real_trips": n_trips, "days_covered": covered,
                          "ready": n_trips >= tcfg["min_real_trips"] and covered >= tcfg["min_days_covered"],
                          "status": "ready" if n_trips >= tcfg["min_real_trips"] and covered >= tcfg["min_days_covered"]
                          else ("no_data" if n_trips == 0 else "collecting"),
                          "trips_to_go": max(0, tcfg["min_real_trips"] - n_trips)}
        for g in GROUPS:
            s = summary[g]
            s["total"] += 1
            s["ready"] += bool(row[g]["ready"])
            s["no_data"] += row[g]["status"] == "no_data"
            p = row[g].get("projected_ready_date")
            if p and (s["latest_projected_ready_date"] is None or p > s["latest_projected_ready_date"]):
                s["latest_projected_ready_date"] = p
        mandis.append(row)
    return {"as_of": today, "config_file": Path(cfg["_path"]).name,
            "thresholds": {g: {k: v for k, v in cfg[g].items()} for g in GROUPS},
            "summary": summary, "mandis": mandis}


def mandi_price_ready(db: Session, today: date | None = None) -> dict[int, bool]:
    """{mandi_id: prices ready?} — used to stamp real forecasts real vs real_partial."""
    return {m["mandi_id"]: m["prices"]["ready"] for m in compute(db, today)["mandis"]}


def provenance_for(synthetic: bool, ready: bool) -> str:
    """Provenance of an output built for one mandi: any synthetic input wins; real-but-thin is real_partial."""
    if synthetic:
        return SYNTHETIC
    return REAL if ready else REAL_PARTIAL
