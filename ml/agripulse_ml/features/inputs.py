"""Raw inputs for the feature store, in one shape regardless of where they come from.

    Inputs.from_synthetic(seed=7)          pinned synthetic history + SIMULATED weather forecasts
    Inputs.from_db(db, synthetic=False)    real rows (or synthetic rows) from the database

Frames:
    prices     mandi_id, date, price                 (one modal price per mandi-day, outliers removed)
    arrivals   mandi_id, date, tonnes
    weather    mandi_id, date, source, precip_mm, tmax_c   (observed only; source sets the publication lag)
    forecasts  mandi_id, issued_on, target_date, precip_mm, tmax_c   (as issued on that day)
    mandis     mandi_id, district, state, lat, lon
"""
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from agripulse_api.provenance import REAL, REAL_PARTIAL, SYNTHETIC

from .config import features_config


@dataclass
class Inputs:
    prices: pd.DataFrame
    arrivals: pd.DataFrame
    weather: pd.DataFrame
    forecasts: pd.DataFrame
    mandis: pd.DataFrame
    # provenance per input: prices per mandi (real / real_partial / synthetic); others one value
    price_provenance: dict[int, str] = field(default_factory=dict)
    weather_provenance: str = REAL
    forecast_provenance: str = REAL
    notes: list[str] = field(default_factory=list)
    # V2-4: Sentinel-2 cropland NDVI per (scene, district): district, date, ndvi_median, clear_px. Always REAL data.
    satellite: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["district", "date", "ndvi_median", "clear_px"]))
    # V2-5: trips towards mandis (transit/features.py TRIP_COLUMNS, UTC times) + their GPS fixes (trip_id, recorded_at,
    # lat, lon). Synthetic runs simulate trip batches from the synthetic arrivals (a plumbing check, labelled).
    transit_trips: pd.DataFrame = field(default_factory=pd.DataFrame)
    transit_gps: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["trip_id", "recorded_at", "lat", "lon"]))
    transit_provenance: str = REAL

    @classmethod
    def from_synthetic(cls, seed: int = 7, start: date = date(2022, 1, 1), end: date = date(2026, 9, 25),
                       n_mandis: int | None = None, propagation: str = "random") -> "Inputs":
        from ..synthetic import SYNTH_MANDIS as ALL

        from ..synthetic import generate

        SYNTH_MANDIS = ALL[:n_mandis] if n_mandis else ALL
        p, w, a = generate(start, end, seed, SYNTH_MANDIS, propagation=propagation)
        ids = {m[0]: i + 1 for i, m in enumerate(SYNTH_MANDIS)}
        for d in (p, w, a):
            d["mandi_id"] = d["mandi"].map(ids)
        weather = w.assign(source="synthetic")[["mandi_id", "date", "source", "precip_mm", "tmax_c"]]
        mandis = pd.DataFrame([{"mandi_id": ids[m[0]], "district": m[1], "state": "Karnataka", "lat": m[2], "lon": m[3]}
                               for m in SYNTH_MANDIS])
        from ..transit.features import simulate_trips

        arrivals = a[["mandi_id", "date", "tonnes"]]
        trips = simulate_trips(arrivals, mandis, features_config()["transit"], seed)
        return cls(
            transit_trips=trips, transit_provenance=SYNTHETIC,
            prices=p[["mandi_id", "date", "price"]], arrivals=arrivals, weather=weather,
            forecasts=simulate_forecasts(weather, seed), mandis=mandis,
            price_provenance={int(i): SYNTHETIC for i in mandis["mandi_id"]},
            weather_provenance=SYNTHETIC, forecast_provenance=SYNTHETIC,
            notes=[f"synthetic generator seed {seed}, {start}..{end}"
                   + (", PLANTED SIGNAL: spikes propagate by distance (positive control)" if propagation == "distance" else ""),
                   "weather forecasts are SIMULATED (future synthetic observation + lead-dependent error)"],
        )

    @classmethod
    def from_db(cls, db, synthetic: bool = False) -> "Inputs":
        from sqlalchemy import select

        from agripulse_api.models import Mandi, Weather, WeatherForecast
        from agripulse_api.readiness import mandi_price_ready, provenance_for

        from ..data import load_arrivals, load_prices

        prices = load_prices(db, synthetic=synthetic)
        arrivals = load_arrivals(db, synthetic=synthetic)
        wq = select(Weather.mandi_id, Weather.date, Weather.source, Weather.precip_mm, Weather.tmax_c).where(
            Weather.is_forecast.is_(False), (Weather.source == "synthetic") if synthetic else (Weather.source != "synthetic"))
        weather = pd.DataFrame(db.execute(wq).all(), columns=["mandi_id", "date", "source", "precip_mm", "tmax_c"])
        if not weather.empty:
            weather["date"] = pd.to_datetime(weather["date"])
        mandis = pd.DataFrame(db.execute(select(Mandi.id, Mandi.district, Mandi.state, Mandi.lat, Mandi.lon)).all(),
                              columns=["mandi_id", "district", "state", "lat", "lon"])
        notes = []
        if synthetic:
            forecasts = simulate_forecasts(weather, seed=0)
            price_prov = {int(m): SYNTHETIC for m in prices["mandi_id"].unique()} if not prices.empty else {}
            notes.append("weather forecasts are SIMULATED from synthetic observations")
        else:
            fq = select(WeatherForecast.mandi_id, WeatherForecast.issued_on, WeatherForecast.target_date,
                        WeatherForecast.precip_mm, WeatherForecast.tmax_c)
            forecasts = pd.DataFrame(db.execute(fq).all(), columns=["mandi_id", "issued_on", "target_date", "precip_mm", "tmax_c"])
            for c in ("issued_on", "target_date"):
                forecasts[c] = pd.to_datetime(forecasts[c])
            ready = mandi_price_ready(db)
            price_prov = {int(m): provenance_for(False, ready.get(int(m), False)) for m in prices["mandi_id"].unique()} \
                if not prices.empty else {}
            if forecasts.empty:
                notes.append("no archived real weather forecasts yet: known-future weather features are empty")
        prov = SYNTHETIC if synthetic else REAL
        from agripulse_api.models import SatelliteObs

        sat = pd.DataFrame(db.execute(select(SatelliteObs.district, SatelliteObs.date, SatelliteObs.ndvi_median,
                                             SatelliteObs.clear_px, SatelliteObs.in_scene_px)).all(),
                           columns=["district", "date", "ndvi_median", "clear_px", "in_scene_px"])
        sat["date"] = pd.to_datetime(sat["date"])
        trips, gps, tprov = _transit_from_db(db, synthetic)
        return cls(prices=prices, arrivals=arrivals, weather=weather, forecasts=forecasts, mandis=mandis,
                   price_provenance=price_prov, weather_provenance=prov, forecast_provenance=prov, notes=notes,
                   satellite=sat, transit_trips=trips, transit_gps=gps, transit_provenance=tprov)


def _transit_from_db(db, synthetic: bool):
    """Trips that actually started, never mixing simulated and real (rule 1). Real transit is real_partial until
    the readiness monitor's transit threshold (config/readiness.toml) is met for every mandi with trips."""
    from sqlalchemy import select

    from agripulse_api.models import GpsPoint, Trip
    from agripulse_api.readiness import compute

    from ..transit.features import TRIP_COLUMNS

    q = select(Trip.id, Trip.mandi_id, Trip.load_tons, Trip.started_at, Trip.ended_at, Trip.origin_lat, Trip.origin_lon,
               Trip.planned_duration_min, Trip.is_simulated).where(Trip.started_at.is_not(None),
                                                                   Trip.is_simulated.is_(bool(synthetic)))
    trips = pd.DataFrame(db.execute(q).all(), columns=TRIP_COLUMNS)
    for c in ("started_at", "ended_at"):
        trips[c] = pd.to_datetime(trips[c], utc=True)
    gps = pd.DataFrame(columns=["trip_id", "recorded_at", "lat", "lon"])
    if len(trips):
        gq = select(GpsPoint.trip_id, GpsPoint.recorded_at, GpsPoint.lat, GpsPoint.lon).where(
            GpsPoint.trip_id.in_(trips["trip_id"].tolist()))
        gps = pd.DataFrame(db.execute(gq).all(), columns=["trip_id", "recorded_at", "lat", "lon"])
        gps["recorded_at"] = pd.to_datetime(gps["recorded_at"], utc=True)
    if synthetic:
        return trips, gps, SYNTHETIC
    ready = {m["mandi_id"]: m["transit"]["ready"] for m in compute(db)["mandis"]}
    used = set(trips["mandi_id"]) if len(trips) else set()
    return trips, gps, REAL if used and all(ready.get(m, False) for m in used) else REAL_PARTIAL


def simulate_forecasts(weather: pd.DataFrame, seed: int) -> pd.DataFrame:
    """SYNTHETIC ONLY. One forecast per mandi per issue day for leads 1..max_lead_days:
    forecast = observed value on the target day + error that grows with lead time.
    This deliberately uses future synthetic observations: that is what a simulated forecast is.
    It is never used on real data (real runs read the weather_forecasts archive)."""
    if weather.empty:
        return pd.DataFrame(columns=["mandi_id", "issued_on", "target_date", "precip_mm", "tmax_c"])
    cfg = features_config()
    sf, max_lead = cfg["synthetic_forecast"], int(cfg["weather_forecast"]["max_lead_days"])
    rng = np.random.default_rng(seed + int(sf["seed_offset"]))
    obs = (weather.sort_values("date").drop_duplicates(["mandi_id", "date"], keep="last")
           [["mandi_id", "date", "precip_mm", "tmax_c"]])
    frames = []
    for lead in range(1, max_lead + 1):
        f = obs.rename(columns={"date": "target_date"}).copy()
        f["issued_on"] = f["target_date"] - pd.Timedelta(days=lead)
        n = len(f)
        rain = f["precip_mm"].to_numpy(dtype=float)
        rain = rain * np.exp(rng.normal(0, sf["rain_log_sigma_base"] + sf["rain_log_sigma_per_day"] * lead, n))
        rain = rain + np.abs(rng.normal(0, sf["rain_additive_sd_per_day"] * lead, n)) * (rng.random(n) < 0.3)
        f["precip_mm"] = np.round(np.clip(rain, 0, None), 1)
        f["tmax_c"] = np.round(f["tmax_c"].to_numpy(dtype=float) + rng.normal(0, sf["tmax_sd_base"] + sf["tmax_sd_per_day"] * lead, n), 1)
        frames.append(f)
    return pd.concat(frames, ignore_index=True)[["mandi_id", "issued_on", "target_date", "precip_mm", "tmax_c"]]
