"""SYNTHETIC tomato price / weather / arrivals generator.

Exists only so the pipeline can be developed, tested and demoed before real
Agmarknet history has been collected. Everything it writes is tagged
source="synthetic", and any model trained on it is marked trained_on_synthetic,
which the API and UI surface as a "Synthetic data" badge. Never report its
backtest numbers as real results.

Dynamics (loosely shaped on Karnataka tomato behaviour):
  - annual cycle: dearer in monsoon (Jun-Aug) and Oct-Nov, cheap in Feb-Mar
  - a regional factor shared by all mandis + mandi-specific AR(1) noise
  - supply-shock spikes whose hazard rises after heavy-rain anomalies
  - arrivals move against price
"""
from datetime import date

import numpy as np
import pandas as pd

from agripulse_api.seed_data import KARNATAKA_TOMATO_MANDIS

# (name, district, lat, lon, typical modal Rs/quintal) - levels are made up, not measured
SYNTH_MANDIS = [
    (name, district, lat, lon, 1300 + (i * 173) % 1100) for i, (name, district, lat, lon) in enumerate(KARNATAKA_TOMATO_MANDIS)
]


def generate(start: date = date(2022, 1, 1), end: date = date(2026, 9, 25), seed: int = 7, mandis=None):
    rng = np.random.default_rng(seed)
    mandis = mandis or SYNTH_MANDIS
    days = pd.date_range(start, end, freq="D")
    n = len(days)
    doy = days.dayofyear.to_numpy()

    # regional weather: monsoon rain with persistent anomalies
    monsoon = np.clip(np.sin((doy - 150) / 365.25 * 2 * np.pi), 0, None) ** 1.5
    anomaly = np.zeros(n)
    for i in range(1, n):
        anomaly[i] = 0.97 * anomaly[i - 1] + rng.normal(0, 0.25)
    rain_base = 9 * monsoon * np.exp(0.6 * anomaly)

    seasonal = 0.35 * np.sin((doy - 120) / 365.25 * 2 * np.pi) + 0.15 * np.sin((doy - 250) / 365.25 * 4 * np.pi)
    regional = np.zeros(n)
    for i in range(1, n):
        regional[i] = 0.985 * regional[i - 1] + rng.normal(0, 0.035)

    # spike process: hazard up after wet spells (30-day rain well above normal)
    rain30 = pd.Series(rain_base).rolling(30, min_periods=1).sum().to_numpy()
    wet = (rain30 - pd.Series(rain30).rolling(365, min_periods=30).mean().to_numpy()) > 60
    spike = np.zeros(n)
    i = 0
    while i < n:
        hazard = 0.012 if wet[i] else 0.002
        if rng.random() < hazard:
            height = rng.uniform(0.5, 1.1)
            length = int(rng.integers(21, 49))
            ramp = np.concatenate([np.linspace(0, 1, 10), np.ones(max(length - 20, 1)), np.linspace(1, 0, 10)])
            seg = ramp[: n - i] * height
            spike[i : i + len(seg)] = np.maximum(spike[i : i + len(seg)], seg)
            i += len(seg)
        i += 1

    prices, weather, arrivals = [], [], []
    for name, district, lat, lon, level in mandis:
        own = np.zeros(n)
        for t in range(1, n):
            own[t] = 0.9 * own[t - 1] + rng.normal(0, 0.06)
        lag = int(rng.integers(0, 4))  # prices propagate with small delays
        shifted_spike = np.roll(spike, lag)
        logp = np.log(level) + seasonal + regional + own + shifted_spike
        p = np.round(np.exp(logp) / 10) * 10
        present = rng.random(n) > 0.12  # ~12% missing days, like real mandi reporting
        present &= days.dayofweek.to_numpy() != 6  # many yards closed on Sundays
        mrain = np.clip(rain_base * np.exp(rng.normal(0, 0.5, n)) - rng.uniform(0, 2, n), 0, None)
        tmax = 30 + 3 * np.sin((doy - 100) / 365.25 * 2 * np.pi) - 2.5 * monsoon + rng.normal(0, 1.2, n)
        tonnes = np.clip(level / 12 * np.exp(-0.8 * (logp - np.log(level)) + rng.normal(0, 0.2, n)), 1, None)
        for t in range(n):
            weather.append((name, days[t], round(float(mrain[t]), 1), round(float(tmax[t]), 1)))
            if present[t]:
                prices.append((name, days[t], float(p[t])))
                arrivals.append((name, days[t], round(float(tonnes[t]), 1)))

    return (
        pd.DataFrame(prices, columns=["mandi", "date", "price"]),
        pd.DataFrame(weather, columns=["mandi", "date", "precip_mm", "tmax_c"]),
        pd.DataFrame(arrivals, columns=["mandi", "date", "tonnes"]),
    )


def load_into_db(db, start: date = date(2022, 1, 1), end: date | None = None, seed: int = 7) -> dict:
    """Write synthetic history for the seeded mandis (source='synthetic')."""
    from sqlalchemy import delete, select

    from agripulse_api.models import Arrival, Mandi, Price, Weather

    end = end or date.today()
    by_name = {m.name: m for m in db.scalars(select(Mandi))}
    mandis = [m for m in SYNTH_MANDIS if m[0] in by_name]
    prices, weather, arrivals = generate(start, end, seed, mandis)
    for model in (Price, Weather, Arrival):
        db.execute(delete(model).where(model.source == "synthetic"))
    db.bulk_insert_mappings(
        Price,
        [
            dict(mandi_id=by_name[r.mandi].id, commodity="Tomato", variety="Tomato", grade="Local", date=r.date.date(),
                 min_price=r.price * 0.8, max_price=r.price * 1.15, modal_price=r.price, source="synthetic",
                 quality_flags=["synthetic"])
            for r in prices.itertuples()
        ],
    )
    db.bulk_insert_mappings(
        Weather,
        [
            dict(mandi_id=by_name[r.mandi].id, date=r.date.date(), source="synthetic", precip_mm=r.precip_mm, tmax_c=r.tmax_c)
            for r in weather.itertuples()
        ],
    )
    db.bulk_insert_mappings(
        Arrival,
        [dict(mandi_id=by_name[r.mandi].id, commodity="Tomato", date=r.date.date(), tonnes=r.tonnes, source="synthetic")
         for r in arrivals.itertuples()],
    )
    db.commit()
    return {"prices": len(prices), "weather": len(weather), "arrivals": len(arrivals), "mandis": len(mandis)}
