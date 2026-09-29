"""Feature engineering on a daily mandi grid.

Everything at row (mandi, t) uses information available on day t only:
  price lags/rolling stats end at t, weather rolls end at t, arrivals roll end at t.
Targets look forward (t + 7h days) and are only used for training/evaluation.

Targets are log price *ratios* so one model serves mandis with very different price
levels: p_q(t+7h) = p(t) * exp(q-th quantile of log ratio).
"""
import numpy as np
import pandas as pd

HORIZONS = (1, 2, 3, 4)  # weeks
QUANTILES = (0.1, 0.5, 0.9)
MAX_FFILL_DAYS = 3
SPIKE_WINDOW_DAYS = 14

# Approximate festival-demand windows (month, day_from, day_to). Exact dates move with
# the lunar calendar; V1 uses coarse windows and says so.
FESTIVAL_WINDOWS = [
    (1, 10, 17),  # Sankranti / Pongal
    (3, 20, 31),  # Ugadi (approx.)
    (4, 1, 10),  # Ugadi (approx.)
    (8, 15, 31),  # Varamahalakshmi / Ganesha lead-up (approx.)
    (9, 1, 20),  # Ganesha Chaturthi (approx.)
    (10, 1, 31),  # Dasara / Navaratri / Deepavali (approx.)
    (11, 1, 15),  # Deepavali (approx.)
]

FEATURES = [
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "ret_7",
    "ret_28",
    "roll_mean_7_rel",
    "roll_mean_28_rel",
    "vol_28",
    "max_28_rel",
    "min_28_rel",
    "yoy_rel",
    "arrivals_7_rel",
    "arrivals_known",
    "rain_7",
    "rain_30",
    "rain_30_anom",
    "tmax_7",
    "tmax_7_anom",
    "doy_sin",
    "doy_cos",
    "month",
    "festival",
    "season",
    "mandi_price_level",
]


def festival_flag(dates: pd.Series) -> pd.Series:
    m, d = dates.dt.month, dates.dt.day
    flag = pd.Series(False, index=dates.index)
    for mm, d0, d1 in FESTIVAL_WINDOWS:
        flag |= (m == mm) & (d >= d0) & (d <= d1)
    return flag.astype(int)


def season_code(month: pd.Series) -> pd.Series:
    # 0 = kharif (Jun-Oct), 1 = rabi (Nov-Mar), 2 = summer/zaid (Apr-May)
    return np.select([month.between(6, 10), month.isin([11, 12, 1, 2, 3])], [0, 1], default=2)


def daily_grid(prices: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for mid, g in prices.groupby("mandi_id"):
        g = g.set_index("date").sort_index()
        idx = pd.date_range(g.index.min(), g.index.max(), freq="D")
        s = g["price"].reindex(idx)
        observed = s.notna()
        filled = s.ffill(limit=MAX_FFILL_DAYS)
        frames.append(pd.DataFrame({"mandi_id": mid, "date": idx, "price": filled.values, "observed": observed.values}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["mandi_id", "date", "price", "observed"])


def build_features(
    prices: pd.DataFrame,
    weather: pd.DataFrame | None = None,
    arrivals: pd.DataFrame | None = None,
    spike_threshold_pct: float = 30.0,
    with_targets: bool = True,
) -> pd.DataFrame:
    df = daily_grid(prices)
    if df.empty:
        return df
    if weather is not None and len(weather):
        df = df.merge(weather, on=["mandi_id", "date"], how="left")
    else:
        df["precip_mm"], df["tmax_c"] = np.nan, np.nan
    if arrivals is not None and len(arrivals):
        df = df.merge(arrivals, on=["mandi_id", "date"], how="left")
    else:
        df["tonnes"] = np.nan

    out = []
    for _, g in df.groupby("mandi_id", sort=False):
        g = g.sort_values("date").copy()
        p = g["price"]
        lp = np.log(p)
        for k in (1, 7, 14, 28):
            g[f"lag_{k}"] = (lp - lp.shift(k)).values  # log change vs k days ago
        g["ret_7"] = lp.diff(7)
        g["ret_28"] = lp.diff(28)
        g["roll_mean_7_rel"] = lp - np.log(p.rolling(7, min_periods=4).mean())
        g["roll_mean_28_rel"] = lp - np.log(p.rolling(28, min_periods=14).mean())
        g["vol_28"] = lp.diff().rolling(28, min_periods=14).std()
        g["max_28_rel"] = lp - np.log(p.rolling(28, min_periods=14).max())
        g["min_28_rel"] = lp - np.log(p.rolling(28, min_periods=14).min())
        g["yoy_rel"] = lp - lp.shift(364)
        g["mandi_price_level"] = np.log(p.rolling(365, min_periods=60).median())

        t = g["tonnes"]
        g["arrivals_known"] = t.rolling(7, min_periods=1).count().fillna(0).gt(0).astype(int)
        g["arrivals_7_rel"] = np.log1p(t.rolling(7, min_periods=3).mean()) - np.log1p(t.rolling(56, min_periods=14).mean())

        r = g["precip_mm"]
        g["rain_7"] = r.rolling(7, min_periods=4).sum()
        g["rain_30"] = r.rolling(30, min_periods=20).sum()
        month = g["date"].dt.month
        # anomaly vs this mandi's own expanding same-month mean (no look-ahead)
        clim = g.assign(m=month, r30=g["rain_30"]).groupby("m")["r30"].transform(lambda s: s.expanding().mean().shift(1))
        g["rain_30_anom"] = g["rain_30"] - clim
        tx = g["tmax_c"]
        g["tmax_7"] = tx.rolling(7, min_periods=4).mean()
        tclim = g.assign(m=month, t7=g["tmax_7"]).groupby("m")["t7"].transform(lambda s: s.expanding().mean().shift(1))
        g["tmax_7_anom"] = g["tmax_7"] - tclim

        doy = g["date"].dt.dayofyear
        g["doy_sin"], g["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
        g["month"] = month
        g["festival"] = festival_flag(g["date"])
        g["season"] = season_code(month)

        if with_targets:
            for h in HORIZONS:
                g[f"target_h{h}"] = lp.shift(-7 * h) - lp
                g[f"target_date_h{h}"] = g["date"] + pd.Timedelta(days=7 * h)
            fwd_max = p[::-1].rolling(SPIKE_WINDOW_DAYS, min_periods=SPIKE_WINDOW_DAYS).max()[::-1].shift(-1)
            rise = fwd_max / p - 1
            g["spike"] = np.where(rise.notna(), (rise * 100 > spike_threshold_pct).astype(float), np.nan)
        out.append(g)

    feat = pd.concat(out, ignore_index=True)
    feat = feat[feat["price"].notna()].reset_index(drop=True)
    return feat
