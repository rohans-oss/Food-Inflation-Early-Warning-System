from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tracking.geo import haversine_km

from ..db import get_db
from ..models import Forecast, Mandi, Price
from ..rbac import require
from .mandis import mandi_out

router = APIRouter(tags=["prices & forecasts"])


def _latest_price_rows(db: Session, commodity: str):
    """Latest non-outlier day per mandi; one modal per mandi-day (median across varieties)."""
    last_day = (
        select(Price.mandi_id, func.max(Price.date).label("d"))
        .where(Price.commodity == commodity, Price.is_outlier.is_(False))
        .group_by(Price.mandi_id)
        .subquery()
    )
    rows = db.execute(
        select(Price).join(last_day, (Price.mandi_id == last_day.c.mandi_id) & (Price.date == last_day.c.d))
        .where(Price.commodity == commodity, Price.is_outlier.is_(False))
    ).scalars().all()
    by_mandi: dict[int, list[Price]] = {}
    for p in rows:
        by_mandi.setdefault(p.mandi_id, []).append(p)
    return by_mandi


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


@router.get("/prices/latest")
def latest_prices(
    commodity: str = "Tomato",
    state: str | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_km: float = 250,
    db: Session = Depends(get_db),
    _=Depends(require("prices:read")),
):
    out = []
    for mid, rows in _latest_price_rows(db, commodity).items():
        m = db.get(Mandi, mid)
        if state and m.state != state:
            continue
        dist = None
        if near_lat is not None and near_lon is not None:
            if m.lat is None:
                continue
            dist = round(haversine_km(near_lat, near_lon, m.lat, m.lon), 1)
            if dist > radius_km:
                continue
        out.append(
            {
                "mandi": mandi_out(m),
                "date": rows[0].date,
                "modal_price": _median([r.modal_price for r in rows]),
                "min_price": min((r.min_price for r in rows if r.min_price is not None), default=None),
                "max_price": max((r.max_price for r in rows if r.max_price is not None), default=None),
                "unit": "Rs/quintal",
                "varieties": sorted({f"{r.variety} / {r.grade}" for r in rows}),
                "is_synthetic": any(r.source == "synthetic" for r in rows),
                "distance_km": dist,
            }
        )
    out.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0, r["mandi"]["name"]))
    return out


@router.get("/prices/history")
def price_history(
    mandi_id: int,
    commodity: str = "Tomato",
    days: int = 180,
    db: Session = Depends(get_db),
    _=Depends(require("prices:read")),
):
    since = date.today() - timedelta(days=min(days, 3650))
    rows = db.execute(
        select(Price.date, func.avg(Price.modal_price), func.min(Price.min_price), func.max(Price.max_price),
               func.max(Price.source))
        .where(Price.mandi_id == mandi_id, Price.commodity == commodity, Price.date >= since, Price.is_outlier.is_(False))
        .group_by(Price.date)
        .order_by(Price.date)
    ).all()
    return [
        {"date": d, "modal_price": round(m, 0), "min_price": lo, "max_price": hi, "is_synthetic": src == "synthetic"}
        for d, m, lo, hi, src in rows
    ]


def forecast_block(db: Session, mandi_id: int, commodity: str = "Tomato") -> dict | None:
    issue = db.scalar(
        select(func.max(Forecast.issue_date)).where(Forecast.mandi_id == mandi_id, Forecast.commodity == commodity)
    )
    if issue is None:
        return None
    rows = db.scalars(
        select(Forecast)
        .where(Forecast.mandi_id == mandi_id, Forecast.commodity == commodity, Forecast.issue_date == issue)
        .order_by(Forecast.horizon_weeks)
    ).all()
    return {
        "mandi_id": mandi_id,
        "issue_date": issue,
        "model": rows[0].model_name,
        "model_version": rows[0].model_version,
        "trained_on_synthetic": rows[0].trained_on_synthetic,
        "spike_prob_14d": rows[0].spike_prob,
        "unit": "Rs/quintal",
        "horizons": [
            {"weeks": f.horizon_weeks, "target_date": f.target_date, "p10": f.p10, "p50": f.p50, "p90": f.p90}
            for f in rows
        ],
    }


@router.get("/forecasts/{mandi_id}")
def mandi_forecast(mandi_id: int, db: Session = Depends(get_db), _=Depends(require("forecasts:read"))):
    if db.get(Mandi, mandi_id) is None:
        raise HTTPException(404, "Mandi not found")
    block = forecast_block(db, mandi_id)
    if block is None:
        raise HTTPException(404, "No forecast yet for this mandi")
    return block


@router.get("/forecasts")
def all_forecasts(state: str | None = None, db: Session = Depends(get_db), _=Depends(require("forecasts:read"))):
    out = []
    for m in db.scalars(select(Mandi).order_by(Mandi.name)):
        if state and m.state != state:
            continue
        b = forecast_block(db, m.id)
        if b:
            out.append({"mandi": mandi_out(m), **b})
    return out
