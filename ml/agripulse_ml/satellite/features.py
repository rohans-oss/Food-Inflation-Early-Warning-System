"""`satellite` feature group (V2-4): cropland NDVI of the mandi's district, as PUBLISHED by the issue date.

An acquisition dated d is usable from d + lag (config/features.toml publication_lag_days.sentinel2 = 2). At issue date t:
  sat_ndvi_30      clear-pixel-weighted mean NDVI of acquisitions dated in (t - lag - 30, t - lag]
  sat_obs_30       how many usable acquisitions that window had (clouds!)
  sat_ndvi_chg_30  sat_ndvi_30(t) - sat_ndvi_30(t - 30)
  sat_ndvi_anom    sat_ndvi_30(t) - mean of sat_ndvi_30 on the same day in PRIOR years (up to 8)
  sat_age_days     days since the newest usable acquisition
Mandis are matched to satellite districts by district name; mandis outside the covered districts get NaN
(the pilot covers Kolar and Chikkaballapur only).
Data provenance: REAL (Sentinel-2 L2A via Earth Search, ESA WorldCover cropland).
"""
import numpy as np
import pandas as pd

SATELLITE = ["sat_ndvi_30", "sat_obs_30", "sat_ndvi_chg_30", "sat_ndvi_anom", "sat_age_days"]
WINDOW = 30


def best_view_per_day(obs: pd.DataFrame) -> pd.DataFrame:
    """Sentinel-2 tiles overlap, so one district is often seen by 2-3 tiles on the same day, covering the SAME
    fields (first real pilot rows, 2018-01-04 Kolar: 317,677 / 78,818 / 330 cropland pixels in view). Keep the
    most complete view (most cropland pixels in the scene) per district and day; ties -> more clear pixels."""
    if obs.empty or "in_scene_px" not in obs:
        return obs
    o = obs.sort_values(["district", "date", "in_scene_px", "clear_px"], ascending=[True, True, False, False])
    return o.drop_duplicates(["district", "date"], keep="first")


def district_series(obs: pd.DataFrame, idx: pd.DatetimeIndex, lag: int) -> pd.DataFrame:
    """obs for ONE district (date, ndvi_median, clear_px[, in_scene_px]) -> daily features on idx."""
    o = best_view_per_day(obs).dropna(subset=["ndvi_median"])
    o = o[o["clear_px"] > 0]
    full = pd.date_range(min(idx.min(), o["date"].min()) - pd.Timedelta(days=400 * 8), idx.max(), freq="D") \
        if len(o) else idx
    w = o.groupby("date")["clear_px"].sum().reindex(full, fill_value=0).astype(float)
    wx = (o.assign(wx=o["ndvi_median"] * o["clear_px"]).groupby("date")["wx"].sum()).reindex(full, fill_value=0.0)
    n = o.groupby("date").size().reindex(full, fill_value=0).astype(float)
    # usable at t: acquisitions dated <= t - lag  -> shift by lag
    W = w.rolling(WINDOW, min_periods=1).sum().shift(lag)
    WX = wx.rolling(WINDOW, min_periods=1).sum().shift(lag)
    N = n.rolling(WINDOW, min_periods=1).sum().shift(lag)
    ndvi = (WX / W).where(W > 0)
    last = pd.Series(full.where(n.to_numpy() > 0), index=full).ffill().shift(lag)
    out = pd.DataFrame(index=full)
    out["sat_ndvi_30"] = ndvi
    out["sat_obs_30"] = N
    out["sat_ndvi_chg_30"] = ndvi - ndvi.shift(WINDOW)
    prior = pd.concat([ndvi.shift(365 * k) for k in range(1, 9)], axis=1)
    out["sat_ndvi_anom"] = ndvi - prior.mean(axis=1, skipna=True).where(prior.notna().any(axis=1))
    out["sat_age_days"] = (pd.Series(full, index=full) - last).dt.days.astype(float)
    return out.reindex(idx)


def satellite_features(df: pd.DataFrame, satellite: pd.DataFrame, mandis: pd.DataFrame, lag: int) -> pd.DataFrame:
    """df: feature table rows (mandi_id, date). Returns mandi_id, date + SATELLITE columns."""
    district_of = dict(zip(mandis["mandi_id"].astype(int), mandis["district"]))
    frames = []
    dates = pd.DatetimeIndex(sorted(df["date"].unique()))
    by_d = {d: g for d, g in satellite.groupby("district")}
    cache = {}
    for mid in sorted(df["mandi_id"].unique()):
        d = district_of.get(int(mid))
        if d not in by_d:
            continue
        if d not in cache:
            cache[d] = district_series(by_d[d], dates, lag)
        frames.append(cache[d].rename_axis("date").reset_index().assign(mandi_id=int(mid)))
    if not frames:
        return pd.DataFrame(columns=["mandi_id", "date", *SATELLITE])
    return pd.concat(frames, ignore_index=True)[["mandi_id", "date", *SATELLITE]]
