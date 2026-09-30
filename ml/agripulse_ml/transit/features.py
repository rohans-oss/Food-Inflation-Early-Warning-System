"""`transit` feature group (V2-5): produce on the road towards each mandi, as KNOWN at the snapshot.

Snapshot for issue date t = 00:00 IST of day t. A trip counts only through what was recorded before the snapshot:
  in transit at S   started_at < S and not ended before S (ended_at is NULL or >= S)
  its position      the last GPS fix recorded before S (else the trip's origin)
  its ETA           S + remaining road km / speed, from that position. The actual ended_at is NEVER used for ETA:
                    it lies in the future at S (tests/test_transit.py plants exactly that leak and catches it).
                    Without GPS (synthetic batches), ETA = started_at + planned duration, fixed when the trip starts.
Columns (past-only):
  tr_tons_now        tonnes in transit to the mandi at S
  tr_tons_eta_24     ... of which expected within 24 h;  tr_tons_eta_72 within 72 h
  tr_trips_now       trips in transit
  tr_tons_recent_7   tonnes delivered in the 7 days before S (trip ended before S)
  tr_tracked         1 if any trip to this mandi had started before S (0 = no tracking yet, not "no supply")
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd

TRANSIT = ["tr_tons_now", "tr_tons_eta_24", "tr_tons_eta_72", "tr_trips_now", "tr_tons_recent_7", "tr_tracked"]
IST = timezone(timedelta(hours=5, minutes=30))
TRIP_COLUMNS = ["trip_id", "mandi_id", "load_tons", "started_at", "ended_at", "origin_lat", "origin_lon",
                "planned_duration_min", "is_simulated"]


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def snapshot(day: pd.Timestamp) -> pd.Timestamp:
    """00:00 IST of the issue date, as a UTC timestamp (trip times are stored in UTC)."""
    return pd.Timestamp(day.date()).tz_localize(IST).tz_convert("UTC")


def transit_features(df: pd.DataFrame, trips: pd.DataFrame, gps: pd.DataFrame, mandis: pd.DataFrame,
                     cfg: dict) -> pd.DataFrame:
    """df: table rows (mandi_id, date). trips: TRIP_COLUMNS (UTC timestamps). gps: trip_id, recorded_at, lat, lon."""
    out_cols = ["mandi_id", "date", *TRANSIT]
    if trips.empty:
        return pd.DataFrame(columns=out_cols)
    speed, factor = float(cfg["speed_kmph"]), float(cfg["road_factor"])
    w24, w72 = (float(h) for h in cfg["eta_windows_h"])
    recent = pd.Timedelta(days=int(cfg["recent_days"]))
    m = mandis.set_index("mandi_id")
    t = trips[trips["mandi_id"].isin(m.index)].copy()
    t["dest_lat"], t["dest_lon"] = t["mandi_id"].map(m["lat"]), t["mandi_id"].map(m["lon"])
    t = t.dropna(subset=["started_at"])
    g = gps.sort_values("recorded_at") if len(gps) else gps
    rows = []
    for day in pd.DatetimeIndex(sorted(df["date"].unique())):
        S = snapshot(day)
        started = t[t["started_at"] < S]
        if started.empty:
            continue
        live = started[started["ended_at"].isna() | (started["ended_at"] >= S)].copy()
        done = started[started["ended_at"].notna() & (started["ended_at"] < S) & (started["ended_at"] >= S - recent)]
        if len(live):
            # hours to arrival as seen at S: planned (fixed when the trip started), or from the last GPS fix before S
            hours = ((live["started_at"] - S).dt.total_seconds() / 3600
                     + live["planned_duration_min"].fillna(0).astype(float) / 60).to_numpy(copy=True)
            if len(g):
                last = g[(g["recorded_at"] < S) & g["trip_id"].isin(live["trip_id"])].groupby("trip_id").last()
                has = live["trip_id"].isin(last.index).to_numpy()
                if has.any():
                    lv = live[has]
                    km = _haversine_km(lv["trip_id"].map(last["lat"]).to_numpy(float),
                                       lv["trip_id"].map(last["lon"]).to_numpy(float),
                                       lv["dest_lat"].to_numpy(float), lv["dest_lon"].to_numpy(float)) * factor
                    hours[has] = km / speed
            hours = np.maximum(hours, 0.0)  # overdue trips are "arriving now", never in the past
            live = live.assign(h=hours)
        tracked = set(started["mandi_id"])
        agg_live = live.groupby("mandi_id").agg(now=("load_tons", "sum"), n=("trip_id", "count")) if len(live) else None
        e24 = live[live["h"] <= w24].groupby("mandi_id")["load_tons"].sum() if len(live) else None
        e72 = live[live["h"] <= w72].groupby("mandi_id")["load_tons"].sum() if len(live) else None
        rec = done.groupby("mandi_id")["load_tons"].sum()
        for mid in tracked:
            rows.append((mid, day,
                         float(agg_live["now"].get(mid, 0.0)) if agg_live is not None else 0.0,
                         float(e24.get(mid, 0.0)) if e24 is not None else 0.0,
                         float(e72.get(mid, 0.0)) if e72 is not None else 0.0,
                         float(agg_live["n"].get(mid, 0)) if agg_live is not None else 0.0,
                         float(rec.get(mid, 0.0)), 1.0))
    return pd.DataFrame(rows, columns=out_cols)


def simulate_trips(arrivals: pd.DataFrame, mandis: pd.DataFrame, cfg: dict, seed: int) -> pd.DataFrame:
    """SYNTHETIC trip batches carrying the synthetic arrivals: each arrival day's tonnage is split into batches that
    leave min_lead_h..max_lead_h before a random arrival time that day. Labelled synthetic."""
    s = cfg["synthetic"]
    rng = np.random.default_rng(seed + int(s["seed_offset"]))
    a = arrivals.dropna(subset=["tonnes"])
    k = int(s["batches_per_day"])
    rep = a.loc[a.index.repeat(k)].reset_index(drop=True)
    rep["load_tons"] = rep["tonnes"] / k
    arrive = (pd.to_datetime(rep["date"]).dt.tz_localize(IST)
              + pd.to_timedelta(rng.uniform(5, 20, len(rep)), unit="h")).dt.tz_convert("UTC")
    lead = pd.Series(pd.to_timedelta(rng.uniform(float(s["min_lead_h"]), float(s["max_lead_h"]), len(rep)), unit="h"))
    m = mandis.set_index("mandi_id")
    return pd.DataFrame({
        "trip_id": np.arange(len(rep)), "mandi_id": rep["mandi_id"].astype(int).to_numpy(),
        "load_tons": rep["load_tons"].to_numpy(), "started_at": (arrive - lead).to_numpy(),
        "ended_at": arrive.to_numpy(),
        "origin_lat": rep["mandi_id"].map(m["lat"]).to_numpy(), "origin_lon": rep["mandi_id"].map(m["lon"]).to_numpy(),
        "planned_duration_min": (lead.dt.total_seconds() / 60).to_numpy()
        * rng.uniform(*[float(x) for x in s["plan_error"]], len(rep)), "is_simulated": True})
