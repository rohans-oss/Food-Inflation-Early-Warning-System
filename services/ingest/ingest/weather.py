"""Weather ingestion for every mandi with coordinates.

Open-Meteo  GET https://api.open-meteo.com/v1/forecast
    ?latitude&longitude&daily=precipitation_sum,temperature_2m_max,temperature_2m_min,
     relative_humidity_2m_mean&timezone=Asia/Kolkata&past_days=..&forecast_days=..
    -> {"daily": {"time": ["YYYY-MM-DD", ...], "<var>": [...]}}
NASA POWER  GET https://power.larc.nasa.gov/api/temporal/daily/point
    ?parameters=T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN&community=AG
     &latitude&longitude&start=YYYYMMDD&end=YYYYMMDD&format=JSON
    -> {"properties": {"parameter": {"<PARAM>": {"YYYYMMDD": value}}}}
    Missing values come back as -999 (NASA POWER's fill value) and are stored as NULL.
"""
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.models import Mandi, Weather

from .runs import tracked_run

OPEN_METEO_DAILY = ["precipitation_sum", "temperature_2m_max", "temperature_2m_min", "relative_humidity_2m_mean"]
NASA_PARAMS = ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "ALLSKY_SFC_SW_DWN"]
NASA_FILL = -999.0


def _mandis_with_coords(db: Session) -> list[Mandi]:
    return list(db.scalars(select(Mandi).where(Mandi.lat.is_not(None), Mandi.lon.is_not(None))))


def _upsert(db: Session, mandi_id: int, day: date, source: str, **vals) -> None:
    w = db.scalar(select(Weather).where(Weather.mandi_id == mandi_id, Weather.date == day, Weather.source == source))
    if w is None:
        w = Weather(mandi_id=mandi_id, date=day, source=source)
        db.add(w)
    for k, v in vals.items():
        setattr(w, k, v)


def parse_open_meteo(payload: dict, today: date) -> list[dict]:
    d = payload["daily"]
    out = []
    for i, t in enumerate(d["time"]):
        day = date.fromisoformat(t)
        out.append(
            {
                "date": day,
                "is_forecast": day > today,
                "precip_mm": d.get("precipitation_sum", [None] * (i + 1))[i],
                "tmax_c": d.get("temperature_2m_max", [None] * (i + 1))[i],
                "tmin_c": d.get("temperature_2m_min", [None] * (i + 1))[i],
                "rh_pct": d.get("relative_humidity_2m_mean", [None] * (i + 1))[i],
            }
        )
    return out


def run_open_meteo(db: Session, client: httpx.Client | None = None, past_days: int = 7, forecast_days: int = 16) -> dict:
    s = get_settings()
    own = client is None
    client = client or httpx.Client()
    today = date.today()
    try:
        with tracked_run(db, "open_meteo") as run:
            n, failed = 0, {}
            for m in _mandis_with_coords(db):
                try:
                    r = client.get(
                        s.open_meteo_url,
                        params={
                            "latitude": m.lat,
                            "longitude": m.lon,
                            "daily": ",".join(OPEN_METEO_DAILY),
                            "timezone": "Asia/Kolkata",
                            "past_days": past_days,
                            "forecast_days": forecast_days,
                        },
                        timeout=30,
                    )
                    r.raise_for_status()
                    rows = parse_open_meteo(r.json(), today)
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    failed[m.name] = str(exc)[:200]
                    continue
                for row in rows:
                    _upsert(db, m.id, row.pop("date"), "open_meteo", **row)
                    n += 1
            if failed and n == 0:
                raise RuntimeError(f"Open-Meteo failed for every mandi: {list(failed.items())[:3]}")
            run.rows, run.details = n, {"failed_mandis": failed}
            return run.details | {"rows": n}
    finally:
        if own:
            client.close()


def parse_nasa_power(payload: dict) -> list[dict]:
    params = payload["properties"]["parameter"]

    def val(p: str, key: str):
        v = params.get(p, {}).get(key)
        return None if v is None or float(v) <= NASA_FILL + 1e-6 else float(v)

    keys = sorted(next(iter(params.values())).keys())
    return [
        {
            "date": datetime.strptime(k, "%Y%m%d").date(),
            "tmax_c": val("T2M_MAX", k),
            "tmin_c": val("T2M_MIN", k),
            "precip_mm": val("PRECTOTCORR", k),
            "rh_pct": val("RH2M", k),
            "solar_kwh_m2": val("ALLSKY_SFC_SW_DWN", k),
        }
        for k in keys
    ]


def run_nasa_power(db: Session, client: httpx.Client | None = None, days_back: int = 30, start: date | None = None) -> dict:
    s = get_settings()
    own = client is None
    client = client or httpx.Client()
    end = date.today() - timedelta(days=1)
    start = start or end - timedelta(days=days_back)
    try:
        with tracked_run(db, "nasa_power") as run:
            n, failed = 0, {}
            for m in _mandis_with_coords(db):
                try:
                    r = client.get(
                        s.nasa_power_url,
                        params={
                            "parameters": ",".join(NASA_PARAMS),
                            "community": "AG",
                            "latitude": m.lat,
                            "longitude": m.lon,
                            "start": start.strftime("%Y%m%d"),
                            "end": end.strftime("%Y%m%d"),
                            "format": "JSON",
                        },
                        timeout=60,
                    )
                    r.raise_for_status()
                    rows = parse_nasa_power(r.json())
                except (httpx.HTTPError, KeyError, ValueError, StopIteration) as exc:
                    failed[m.name] = str(exc)[:200]
                    continue
                for row in rows:
                    _upsert(db, m.id, row.pop("date"), "nasa_power", **row)
                    n += 1
            if failed and n == 0:
                raise RuntimeError(f"NASA POWER failed for every mandi: {list(failed.items())[:3]}")
            run.rows, run.details = n, {"failed_mandis": failed, "start": str(start), "end": str(end)}
            return run.details | {"rows": n}
    finally:
        if own:
            client.close()
