"""Pre-V3 B-1: model-agnostic interval calibration from the model's own track record.

Any quantile model (naive, V1 LightGBM, TFT, GNN) produces p10 / p50 / p90. This module widens or narrows p10 and
p90 using how the SAME model's past forecasts actually fared, so it wraps every model identically and needs nothing
from the model itself. It works on a "stream" of past forecasts: one row per (mandi, issue date, horizon) with

    date          issue date t
    known_on      date the actual became published (target date + publication lag)
    horizon       1..4
    y             actual log(price[t+7h] / base)          (log(actual) also works: everything is a difference)
    q10, q50, q90 forecast log quantiles, same scale as y

Conformity scores (conformalized quantile regression, per side): lo = q10 - y (> 0 when the price fell below the
range), hi = y - q90 (> 0 when above). At issue date t, with the scores of rows known before t and issued within
`window_days`:
    track_record  offset_side = empirical (1 - a/2) quantile of that side's scores, a = 1 - target (split-conformal
                  finite-sample level); new q10 = q10 - offset_lo, new q90 = q90 + offset_hi.
    aci           the same, but the level adapts online (Gibbs & Candes 2021): once per forecasting round whose
                  outcomes have just been published, a_side <- a_side + gamma * (a/2 - that round's miss rate).
Fewer than `min_scores` usable scores for a horizon -> status "not_yet_applicable" and the range is left unchanged.
Leakage rule: a score is used at t only if known_on < t (tests/test_calibration.py tampers with the future).
"""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "calibration.toml"
APPLIED, NOT_YET, NONE = "applied", "not_yet_applicable", "none"


@lru_cache
def calibration_config() -> dict:
    path = Path(os.environ.get("CALIBRATION_CONFIG", _DEFAULT))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def _level(n: int, miss: float) -> float:
    """Finite-sample split-conformal quantile level for a one-sided miss rate `miss`."""
    return min(1.0, (1 - miss) * (n + 1) / n)


def calibrate_stream(stream: pd.DataFrame, cfg: dict | None = None, method: str | None = None) -> pd.DataFrame:
    """Returns stream + q10_cal, q90_cal, calibration (status), off_lo, off_hi, n_scores. Rows are calibrated from
    the scores of OTHER rows published before their issue date only."""
    c = (cfg or calibration_config())["calibration"]
    method = method or c["method"]
    miss = (1 - float(c["target_coverage_pct"]) / 100) / 2
    window = pd.Timedelta(days=int(c["window_days"]))
    min_n = int(c["min_scores"])
    gamma = float(c.get("aci", {}).get("gamma", 0.005))
    out = []
    for h, g in stream.groupby("horizon", sort=True):
        g = g.sort_values("date").copy()
        lo_s = (g["q10"] - g["y"]).to_numpy()
        hi_s = (g["y"] - g["q90"]).to_numpy()
        known = g["known_on"].to_numpy()
        issued = g["date"].to_numpy()
        has_y = ~np.isnan(g["y"].to_numpy())
        off_lo = np.zeros(len(g))
        off_hi = np.zeros(len(g))
        n_sc = np.zeros(len(g), dtype=int)
        status = np.full(len(g), NOT_YET, dtype=object)
        a_lo = a_hi = miss  # ACI state per side
        seen = np.zeros(len(g), dtype=bool)  # outcomes already fed to ACI
        dates = np.unique(issued)
        pos = {d: np.where(issued == d)[0] for d in dates}
        for d in dates:
            usable = has_y & (known < d) & (issued >= d - window)
            n = int(usable.sum())
            rows = pos[d]
            n_sc[rows] = n
            if method == "aci":
                # one update per forecasting ROUND (issue date), as in Gibbs & Candes: the outcomes published since
                # the last round, grouped by the round that issued them, each round's miss RATE counts once. Only
                # rows that were actually calibrated feed the update (warm-up rows are not the method's output).
                new = has_y & (known < d) & ~seen
                idx = np.where(new)[0]
                for r in np.unique(issued[idx]):
                    b = idx[issued[idx] == r]
                    b = b[status[b] == APPLIED]
                    if len(b):
                        a_lo += gamma * (miss - float(np.mean(lo_s[b] > off_lo[b])))
                        a_hi += gamma * (miss - float(np.mean(hi_s[b] > off_hi[b])))
                seen[idx] = True
                a_lo, a_hi = float(np.clip(a_lo, 1e-3, 0.5)), float(np.clip(a_hi, 1e-3, 0.5))
            if n < min_n:
                continue
            lv_lo = _level(n, a_lo if method == "aci" else miss)
            lv_hi = _level(n, a_hi if method == "aci" else miss)
            off_lo[rows] = np.quantile(lo_s[usable], lv_lo)
            off_hi[rows] = np.quantile(hi_s[usable], lv_hi)
            status[rows] = APPLIED
        g["off_lo"], g["off_hi"], g["n_scores"], g["calibration"] = off_lo, off_hi, n_sc, status
        g["q10_cal"] = np.minimum(g["q10"] - g["off_lo"], g["q50"])
        g["q90_cal"] = np.maximum(g["q90"] + g["off_hi"], g["q50"])
        out.append(g)
    return pd.concat(out).sort_index()


def stream_from_predictions(preds: pd.DataFrame, label_lag_days: int) -> pd.DataFrame:
    """Harness predictions frame (one row per mandi x issue date, columns y_h{h}, q10_h{h}, ...) -> long stream."""
    rows = []
    for h in (1, 2, 3, 4):
        rows.append(pd.DataFrame({
            "row": preds.index, "mandi_id": preds["mandi_id"].to_numpy(), "date": preds["date"].to_numpy(),
            "known_on": (preds["date"] + pd.Timedelta(days=7 * h + label_lag_days)).to_numpy(), "horizon": h,
            "y": preds[f"y_h{h}"].to_numpy(float), "q10": preds[f"q10_h{h}"].to_numpy(float),
            "q50": preds[f"q50_h{h}"].to_numpy(float), "q90": preds[f"q90_h{h}"].to_numpy(float)}))
    return pd.concat(rows, ignore_index=True)


def apply_to_predictions(preds: pd.DataFrame, label_lag_days: int, cfg: dict | None = None,
                         method: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calibrate one model's harness predictions; returns (calibrated predictions frame, long stream with status)."""
    s = calibrate_stream(stream_from_predictions(preds, label_lag_days), cfg, method)
    out = preds.copy()
    for h in (1, 2, 3, 4):
        sh = s[s["horizon"] == h].set_index("row")
        out.loc[sh.index, f"q10_h{h}"] = sh["q10_cal"]
        out.loc[sh.index, f"q90_h{h}"] = sh["q90_cal"]
        out.loc[sh.index, f"calibration_h{h}"] = sh["calibration"]
    return out, s


def serving_offsets(db, model_name: str, synthetic: bool, as_of, label_lag_days: int = 1,
                    cfg: dict | None = None) -> dict[int, dict]:
    """Live system: offsets for forecasts issued on `as_of`, from this model's own stored past forecasts and the prices
    that have since been published (same provenance kind: synthetic forecasts vs synthetic prices, real vs real).
    Uses p10_raw / p90_raw so the calibrator never learns from its own output. Returns {h: {off_lo, off_hi,
    calibration, n_scores}}."""
    from sqlalchemy import select

    from agripulse_api.models import Forecast

    from .data import load_prices

    cfg = cfg or calibration_config()
    c = cfg["calibration"]
    as_of = pd.Timestamp(as_of)
    since = (as_of - pd.Timedelta(days=int(c["window_days"]) + 35)).date()
    q = select(Forecast.mandi_id, Forecast.issue_date, Forecast.target_date, Forecast.horizon_weeks, Forecast.p10,
               Forecast.p10_raw, Forecast.p50, Forecast.p90, Forecast.p90_raw).where(
        Forecast.model_name == model_name, Forecast.trained_on_synthetic.is_(bool(synthetic)), Forecast.issue_date >= since)
    f = pd.DataFrame(db.execute(q).all(), columns=["mandi_id", "date", "target_date", "horizon", "p10", "p10_raw", "p50",
                                                   "p90", "p90_raw"])
    out = {h: {"off_lo": 0.0, "off_hi": 0.0, "calibration": NOT_YET, "n_scores": 0} for h in (1, 2, 3, 4)}
    if f.empty:
        return out
    prices = load_prices(db, synthetic=synthetic)
    if prices.empty:
        return out
    f["date"], f["target_date"] = pd.to_datetime(f["date"]), pd.to_datetime(f["target_date"])
    act = prices.rename(columns={"date": "target_date", "price": "actual"})
    f = f.merge(act, on=["mandi_id", "target_date"], how="left")
    lo = f["p10_raw"].fillna(f["p10"])
    hi = f["p90_raw"].fillna(f["p90"])
    stream = pd.DataFrame({"date": f["date"], "known_on": f["target_date"] + pd.Timedelta(days=label_lag_days),
                           "horizon": f["horizon"], "y": np.log(f["actual"]), "q10": np.log(lo),
                           "q50": np.log(f["p50"]), "q90": np.log(hi)})
    probe = pd.DataFrame({"date": as_of, "known_on": as_of + pd.Timedelta(days=3650), "horizon": [1, 2, 3, 4],
                          "y": np.nan, "q10": 0.0, "q50": 0.0, "q90": 0.0, "probe": True})
    s = calibrate_stream(pd.concat([stream.assign(probe=False), probe], ignore_index=True), cfg)
    for r in s[s["probe"].astype(bool)].itertuples():
        out[int(r.horizon)] = {"off_lo": float(r.off_lo), "off_hi": float(r.off_hi), "calibration": r.calibration,
                               "n_scores": int(r.n_scores)}
    return out


def calibrate_range(p10_raw: float, p50: float, p90_raw: float, off: dict) -> tuple[float, float]:
    """Apply log-space offsets to one forecast's raw range; keeps p10 <= p50 <= p90."""
    if off["calibration"] != APPLIED:
        return p10_raw, p90_raw
    return min(p10_raw * float(np.exp(-off["off_lo"])), p50), max(p90_raw * float(np.exp(off["off_hi"])), p50)
