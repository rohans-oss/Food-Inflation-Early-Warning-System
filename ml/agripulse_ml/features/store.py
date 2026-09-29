"""V2 feature store: group-based, publication-lag-aware training tables.

    table = build_table(Inputs.from_synthetic(seed=7), "prices+weather")
    table.df                  one row per (mandi_id, issue date)
    table.columns             {"static": [...], "known_future": [...], "past_only": [...]}
    table.fold_spec()         FoldSpec whose label_lag_days matches the price publication lag
    table.data_card()         rows, date range, missingness, provenance, groups, lags

Time rules (V2 rule 12). A row is an ISSUE DATE t, the day a forecast would be made.
  * A value dated d is usable at t only if d + lag(source) <= t (config/features.toml).
  * The base price b(t) is the last PUBLISHED price: the grid price on day t - lag(prices).
  * Target h: y_h = log(p(t + 7h) / b(t)). That label is only known on t + 7h + lag(prices);
    FoldSpec(label_lag_days=lag) keeps unknown labels out of training.
  * Known-future inputs (calendar, weather forecasts) use only forecasts ISSUED on or before t.

Groups: prices (past-only: price history + arrivals), weather (past-only observations + known-future
forecasts), calendar (known-future, always on), graph (V2-3, past-only: neighbour signals, see graph/features.py).
satellite / transit arrive in later phases.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from agripulse_api.provenance import LABEL, REAL, worst

from ..eval.folds import FoldSpec
from .config import check_built, feature_set_name, features_config, lag, parse_feature_set
from .inputs import Inputs
from .legacy import HORIZONS, MAX_FFILL_DAYS, SPIKE_WINDOW_DAYS, festival_flag, season_code

FORECAST_MAX_AGE_DAYS = 3  # use a forecast issued up to 3 days before t if t's own is missing
STATIC = ["mandi_id", "st_district", "st_state", "st_lat", "st_lon"]
CALENDAR = ["cal_doy_sin", "cal_doy_cos", "cal_month", "cal_festival", "cal_festival_next14", "cal_season"]
PRICES = ["px_chg_1", "px_chg_7", "px_chg_14", "px_chg_28", "px_rel_mean_7", "px_rel_mean_28", "px_vol_28",
          "px_rel_max_28", "px_rel_min_28", "px_yoy", "px_level", "arr_7_rel", "arr_known"]
WEATHER_PAST = ["wx_rain_7", "wx_rain_30", "wx_rain_30_anom", "wx_tmax_7", "wx_tmax_7_anom"]
WEATHER_FUTURE = ["wf_rain_next7", "wf_rain_next14", "wf_tmax_next7", "wf_age_days", "wf_available"]  # windows fixed at 7/14
from ..graph.features import GRAPH  # noqa: E402

GROUP_COLUMNS = {"prices": {"past_only": PRICES},
                 "weather": {"past_only": WEATHER_PAST, "known_future": WEATHER_FUTURE},
                 "graph": {"past_only": GRAPH}}


@dataclass
class FeatureTable:
    df: pd.DataFrame
    feature_set: str
    groups: list[str]
    columns: dict[str, list[str]]
    data_provenance: str
    group_provenance: dict[str, str]
    mandi_provenance: dict[int, str]
    lags: dict[str, int]
    label_lag_days: int
    notes: list[str] = field(default_factory=list)
    graphs: list = field(default_factory=list)  # graph group: the MandiGraph snapshots behind the features

    @property
    def feature_columns(self) -> list[str]:
        """Model inputs (numeric). Static strings (district/state) are for sequence models' embeddings."""
        return [c for role in ("static", "known_future", "past_only") for c in self.columns[role]
                if c not in ("st_district", "st_state")]

    def fold_spec(self, **kw) -> FoldSpec:
        return FoldSpec(label_lag_days=self.label_lag_days, **kw)

    def data_card(self) -> dict:
        d = self.df
        cols = [c for role in ("static", "known_future", "past_only") for c in self.columns[role]]
        miss = (d[cols].isna().mean() * 100).round(1)
        return {
            "feature_set": self.feature_set, "groups": self.groups + ["calendar (always on)"],
            "data_provenance": self.data_provenance, "provenance_label": LABEL[self.data_provenance],
            "group_provenance": self.group_provenance,
            "rows": int(len(d)), "mandis": int(d["mandi_id"].nunique()),
            "date_range": [str(d["date"].min().date()), str(d["date"].max().date())] if len(d) else None,
            "columns": self.columns, "n_features": len(self.feature_columns),
            "missing_pct": {c: float(v) for c, v in miss.items()},
            "target_coverage_pct": {f"h{h}": round(float(d[f"target_h{h}"].notna().mean() * 100), 1) for h in HORIZONS},
            "spike_rate_pct": round(float(d["spike"].mean() * 100), 1) if d["spike"].notna().any() else None,
            "publication_lag_days": self.lags, "label_lag_days": self.label_lag_days, "notes": self.notes,
        }

    def save(self, path: str | Path) -> tuple[Path, Path]:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_parquet(path, index=False)
        card = path.with_suffix(".card.json")
        card.write_text(json.dumps(self.data_card(), indent=2, default=str, ensure_ascii=False))
        return path, card


# ------------------------------------------------------------------ helpers


def _lagged_window(vals: dict[int, pd.Series], window: int, min_count: int) -> tuple[pd.Series, pd.Series]:
    """Mean and count over a trailing window, where each series in `vals` (keyed by its publication lag)
    only contributes values published by t: series with lag L is shifted by L before rolling."""
    total, count = None, None
    for L, s in vals.items():
        sh = s.shift(L)
        su = sh.rolling(window, min_periods=1).sum()
        cn = sh.notna().astype(float).rolling(window, min_periods=1).sum()
        total = su if total is None else total.add(su, fill_value=0)
        count = cn if count is None else count.add(cn, fill_value=0)
    mean = (total / count).where(count >= min_count)
    return mean, count


def _weather_by_lag(wm: pd.DataFrame, idx: pd.DatetimeIndex, col: str) -> dict[int, pd.Series]:
    """Per day, use the source that is published soonest (smallest lag); split the resulting series by lag."""
    if wm.empty:
        return {0: pd.Series(np.nan, index=idx)}
    w = wm.dropna(subset=[col]).copy()
    w["lag"] = w["source"].map(lambda s: lag("synthetic_weather" if s == "synthetic" else s))
    w = w.sort_values(["date", "lag"]).drop_duplicates("date", keep="first")
    out = {}
    for L, g in w.groupby("lag"):
        out[int(L)] = g.set_index("date")[col].reindex(idx)
    return out


def forecast_aggregates(fc: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    """Per (mandi, issue date): forecast rain over the next w days and mean tmax over the next 7 days.
    A window counts only if every lead day 1..w is present (otherwise NaN, never a partial sum)."""
    cols = ["mandi_id", "issued_on"] + [f"wf_rain_next{w}" for w in windows] + ["wf_tmax_next7"]
    if fc.empty:
        return pd.DataFrame(columns=cols)
    f = fc.copy()
    f["lead"] = (f["target_date"] - f["issued_on"]).dt.days
    f = f[(f["lead"] >= 1) & (f["lead"] <= max(max(windows), 7))]
    aggs = {}
    for w in windows:
        m = f["lead"] <= w
        f[f"r{w}"] = f["precip_mm"].where(m)
        f[f"n{w}"] = (m & f["precip_mm"].notna()).astype(int)
        aggs |= {f"r{w}": "sum", f"n{w}": "sum"}
    m7 = f["lead"] <= 7
    f["t7"] = f["tmax_c"].where(m7)
    f["tn7"] = (m7 & f["tmax_c"].notna()).astype(int)
    aggs |= {"t7": "sum", "tn7": "sum"}
    g = f.groupby(["mandi_id", "issued_on"], as_index=False).agg(aggs)
    for w in windows:
        g[f"wf_rain_next{w}"] = g[f"r{w}"].where(g[f"n{w}"] == w)
    g["wf_tmax_next7"] = (g["t7"] / 7).where(g["tn7"] == 7)
    return g[cols].sort_values("issued_on")


def _forecast_features(agg: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Known-future weather at issue date t, from the latest forecast issued on or before t (max 3 days old)."""
    grid = pd.DataFrame({"date": idx})
    if agg.empty:
        out = pd.DataFrame(np.nan, index=idx, columns=WEATHER_FUTURE)
        out["wf_available"] = 0
        return out
    m = pd.merge_asof(grid, agg.drop(columns="mandi_id"), left_on="date", right_on="issued_on", direction="backward",
                      tolerance=pd.Timedelta(days=FORECAST_MAX_AGE_DAYS))
    m["wf_age_days"] = (m["date"] - m["issued_on"]).dt.days
    m["wf_available"] = m["issued_on"].notna().astype(int)
    m = m.set_index("date")
    for c in WEATHER_FUTURE:
        if c not in m:
            m[c] = np.nan
    return m[WEATHER_FUTURE]


def _calendar(idx: pd.DatetimeIndex) -> pd.DataFrame:
    d = pd.Series(idx, index=idx)
    doy = d.dt.dayofyear
    fest = festival_flag(d)
    out = pd.DataFrame(index=idx)
    out["cal_doy_sin"], out["cal_doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    out["cal_month"] = d.dt.month
    out["cal_festival"] = fest
    # any festival day in (t, t+14]: the calendar is known in advance
    fwd = pd.Series(festival_flag(pd.Series(pd.date_range(idx.min(), idx.max() + pd.Timedelta(days=14)))).to_numpy(),
                    index=pd.date_range(idx.min(), idx.max() + pd.Timedelta(days=14)))
    out["cal_festival_next14"] = fwd[::-1].rolling(14, min_periods=1).max()[::-1].shift(-1).reindex(idx).fillna(0).astype(int)
    out["cal_season"] = season_code(d.dt.month)
    return out


# ------------------------------------------------------------------ builder


def build_table(inputs: Inputs, feature_set="prices+weather", spike_threshold_pct: float = 30.0,
                as_of: pd.Timestamp | None = None) -> FeatureTable:
    """as_of: the day the table is built "as of" (e.g. today in production). Issue dates run up to it, so a
    forecast can still be issued after a day with no price (the base price carries forward up to 3 days).
    Default: last price date + price lag."""
    groups = parse_feature_set(feature_set)
    check_built(groups)
    if "prices" not in groups:
        raise ValueError("The prices group is required: targets are price changes")
    cfg = features_config()
    L_p, L_a = lag("prices"), lag("arrivals")
    windows = [int(w) for w in cfg["weather_forecast"]["windows"]]
    st = inputs.mandis.set_index("mandi_id")
    fc_agg = forecast_aggregates(inputs.forecasts, windows) if "weather" in groups else None
    frames = []
    for mid, pg in inputs.prices.groupby("mandi_id"):
        pg = pg.set_index("date")["price"].sort_index()
        # issue dates run until the last price is published, or to as_of if given
        end = pd.Timestamp(as_of) if as_of is not None else pg.index.max() + pd.Timedelta(days=L_p)
        idx = pd.date_range(pg.index.min(), end, freq="D")
        grid = pg.reindex(idx).ffill(limit=MAX_FFILL_DAYS)  # price as observed on each day
        base = grid.shift(L_p)  # last price PUBLISHED by t
        observed = pg.reindex(idx).notna().shift(L_p, fill_value=False)
        f = pd.DataFrame(index=idx)
        f["mandi_id"], f["price"], f["observed"] = int(mid), base, observed.astype(bool)
        lb = np.log(base)

        # ---- prices group (past-only)
        for k in (1, 7, 14, 28):
            f[f"px_chg_{k}"] = lb - lb.shift(k)
        f["px_rel_mean_7"] = lb - np.log(base.rolling(7, min_periods=4).mean())
        f["px_rel_mean_28"] = lb - np.log(base.rolling(28, min_periods=14).mean())
        f["px_vol_28"] = lb.diff().rolling(28, min_periods=14).std()
        f["px_rel_max_28"] = lb - np.log(base.rolling(28, min_periods=14).max())
        f["px_rel_min_28"] = lb - np.log(base.rolling(28, min_periods=14).min())
        f["px_yoy"] = lb - lb.shift(364)
        f["px_level"] = np.log(base.rolling(365, min_periods=60).median())
        ag = inputs.arrivals[inputs.arrivals["mandi_id"] == mid].set_index("date")["tonnes"].reindex(idx).shift(L_a)
        f["arr_known"] = ag.rolling(7, min_periods=1).count().fillna(0).gt(0).astype(int)
        f["arr_7_rel"] = np.log1p(ag.rolling(7, min_periods=3).mean()) - np.log1p(ag.rolling(56, min_periods=14).mean())

        # ---- weather group
        if "weather" in groups:
            wm = inputs.weather[inputs.weather["mandi_id"] == mid]
            rain = _weather_by_lag(wm, idx, "precip_mm")
            tmax = _weather_by_lag(wm, idx, "tmax_c")
            r7, _ = _lagged_window(rain, 7, 4)
            r30, _ = _lagged_window(rain, 30, 20)
            t7, _ = _lagged_window(tmax, 7, 4)
            f["wx_rain_7"], f["wx_rain_30"], f["wx_tmax_7"] = r7 * 7, r30 * 30, t7
            month = pd.Series(idx.month, index=idx)
            f["wx_rain_30_anom"] = f["wx_rain_30"] - f["wx_rain_30"].groupby(month).transform(lambda s: s.expanding().mean().shift(1))
            f["wx_tmax_7_anom"] = f["wx_tmax_7"] - f["wx_tmax_7"].groupby(month).transform(lambda s: s.expanding().mean().shift(1))
            f = f.join(_forecast_features(fc_agg[fc_agg["mandi_id"] == mid], idx))

        # ---- calendar (always on, known-future)
        f = f.join(_calendar(idx))

        # ---- static
        f["st_district"] = st["district"].get(mid) if mid in st.index else None
        f["st_state"] = st["state"].get(mid) if mid in st.index else None
        f["st_lat"] = st["lat"].get(mid) if mid in st.index else np.nan
        f["st_lon"] = st["lon"].get(mid) if mid in st.index else np.nan

        # ---- targets (never features). Labels are known L_p days after their target date.
        for h in HORIZONS:
            fut = grid.shift(-7 * h)
            f[f"target_h{h}"] = np.log(fut / base)
            f[f"target_date_h{h}"] = idx + pd.Timedelta(days=7 * h)
        fwd_max = grid[::-1].rolling(SPIKE_WINDOW_DAYS, min_periods=SPIKE_WINDOW_DAYS).max()[::-1].shift(-1)
        rise = fwd_max / base - 1
        f["spike"] = np.where(rise.notna(), (rise * 100 > spike_threshold_pct).astype(float), np.nan)

        frames.append(f.rename_axis("date").reset_index())

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        df = df[df["price"].notna()].reset_index(drop=True)
    graphs = []
    if "graph" in groups and len(df):
        from ..graph.features import graph_features

        gf, graphs = graph_features(df, inputs)
        df = df.merge(gf, on=["date", "mandi_id"], how="left")

    columns = {"static": list(STATIC), "known_future": list(CALENDAR), "past_only": []}
    for g in groups:
        for role, cols in GROUP_COLUMNS.get(g, {}).items():
            columns[role] += cols
    group_prov = {"prices": worst(*inputs.price_provenance.values()) if inputs.price_provenance else REAL, "calendar": REAL}
    if "weather" in groups:
        group_prov["weather"] = worst(inputs.weather_provenance, inputs.forecast_provenance)
    if "graph" in groups:  # distance edges are real; correlation / flow edges inherit the price provenance
        group_prov["graph"] = group_prov["prices"]
    mandi_prov = {int(m): worst(inputs.price_provenance.get(int(m), REAL),
                                *(group_prov[g] for g in groups if g != "prices")) for m in df["mandi_id"].unique()} if len(df) else {}
    overall = worst(*group_prov.values(), *mandi_prov.values())
    lags = {k: int(v) for k, v in cfg["publication_lag_days"].items()}
    return FeatureTable(df=df, feature_set=feature_set_name(groups), groups=groups, columns=columns,
                        data_provenance=overall, group_provenance=group_prov, mandi_provenance=mandi_prov,
                        lags=lags, label_lag_days=L_p, notes=list(inputs.notes)
                        + (list(graphs[-1].notes) if graphs else []), graphs=graphs)

