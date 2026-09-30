"""V3-4: event-level spike backtest on REAL prices, with the same pre-registered rules run on synthetic draws
alongside (never blended).

    python -m agripulse_ml.real_backtest                      # real rows in DATABASE_URL -> docs/results/backtest-real.json
    python -m agripulse_ml.real_backtest --provenance synthetic --seeds 1-8   # docs/results/backtest-events-synthetic.csv

Rules fixed BEFORE any real data was looked at (PREREG, also printed in docs/backtest-real.md):
- spike day: the V1 label. Issue date t is a spike day when the highest price in (t, t+14] is > 30% above price(t).
- event: a run of spike days at one mandi; runs less than 7 days apart are one event. Its RISE DATE R is the first
  day after the run starts on which the price is > 30% above the price on the run's first day.
- an event is SCOREABLE for a model when the model issued a forecast on every day of [R-14, R-1] at that mandi.
- detected: at least one alert (spike_prob >= threshold) in [R-14, R-1]. Lead time = R - first such alert (days).
- false alarm: an alert day outside every event's [R-14, R-1] at that mandi. Reported as false-alarm days per
  mandi-year, alert precision (share of alert days inside a window) and alert-day share (an always-on alarm = 100%).
  (Revised 2026-09-30 after the first SYNTHETIC sanity run, before any real rows existed: counting alert EPISODES
  let an always-on alarm score zero false alarms.)
- thresholds: (a) fixed 0.5 (the V1 setting); (b) per fold, the threshold with the best day-level F1 on EARLIER
  folds' predictions whose 14-day label was known before the fold's cutoff (0.5 when there is no such history).
- minimum evidence: fewer than 5 scoreable events -> counts are reported, recall / lead / false-alarm numbers are
  withheld. Under one fold (365 days of training + 28 days of targets) -> "not enough real data", nothing else.
- real folds: every 28-day fold from day 365 on (all the history there is), all scored. Synthetic: V2-0's final 8
  folds are scored and 13 earlier folds build the calibration / threshold track record (as in B-1), 8 draws.
- models: the displayed model (V1 LightGBM; ranges B-1 calibrated for the coverage row) vs naive and seasonal naive,
  through the shared harness. TFT / GNN are not run here (V2 lost to naive on synthetic; re-run them with their own
  experiment commands once this report passes the minimums on real data).
"""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agripulse_api.provenance import LABEL, SYNTHETIC

from .eval import FoldSpec, NotEnoughHistory, run
from .evaluate import V1_FEATURE_SET, V1_MODELS
from .features import build_features
from .models import add_seasonal_naive

ROOT = Path(__file__).resolve().parents[2]
PREREG = {
    "spike_pct": 30.0,
    "spike_window_days": 14,
    "event_gap_days": 7,
    "min_events": 5,
    "fixed_threshold": 0.5,
    "threshold_grid": [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)],
    "label_lag_days": 1,  # Agmarknet publishes a day's prices by the next day
    "real_min_train_days": 365,
    "step_days": 28,
}
DISPLAY = "lightgbm_quantile"
SELF_COLLECTION_START = date(2026, 9, 25)


# ---------- events -------------------------------------------------------------------------------------------------
def find_events(feat: pd.DataFrame, pct: float = PREREG["spike_pct"], gap: int = PREREG["event_gap_days"],
                window: int = PREREG["spike_window_days"]) -> pd.DataFrame:
    """Events from the realised price series (feat = build_features output: mandi_id, date, price, spike)."""
    rows = []
    for mid, g in feat.groupby("mandi_id"):
        g = g.sort_values("date")
        price = pd.Series(g["price"].to_numpy(float), index=pd.DatetimeIndex(g["date"]))
        days = pd.DatetimeIndex(g.loc[g["spike"] == 1, "date"])
        if not len(days):
            continue
        runs, cur = [], [days[0]]
        for d in days[1:]:
            if (d - cur[-1]).days < gap:
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)
        for r in runs:
            start = r[0]
            base = price[start]
            fwd = price[(price.index > start) & (price.index <= start + pd.Timedelta(days=window))]
            above = fwd[fwd > base * (1 + pct / 100)]
            rise = above.index[0] if len(above) else fwd.idxmax()
            rows.append({"mandi_id": int(mid), "start": start, "end": r[-1], "rise_date": rise,
                         "base_price": float(base), "peak_price": float(fwd.max()),
                         "rise_pct": round(100 * (fwd.max() / base - 1), 1)})
    cols = ["mandi_id", "start", "end", "rise_date", "base_price", "peak_price", "rise_pct"]
    return pd.DataFrame(rows, columns=cols)


# ---------- thresholds ---------------------------------------------------------------------------------------------
def _f1(y: np.ndarray, p: np.ndarray, t: float) -> float:
    a = p >= t
    tp = float((a & (y == 1)).sum())
    if tp == 0:
        return 0.0
    return 2 * tp / (a.sum() + (y == 1).sum())


def fold_thresholds(preds: pd.DataFrame, folds: list[dict], lag: int = PREREG["label_lag_days"],
                    window: int = PREREG["spike_window_days"]) -> dict[int, float]:
    """Per fold: best day-level F1 on earlier predictions whose label was known before the fold's cutoff."""
    out = {}
    for f in folds:
        cutoff = pd.Timestamp(f["cutoff"])
        known = preds[(preds["date"] + pd.Timedelta(days=window + lag) < cutoff) & preds["spike"].notna()]
        y, p = known["spike"].to_numpy(), known["spike_prob"].to_numpy()
        if len(known) < 50 or not (y == 1).any():
            out[f["fold"]] = PREREG["fixed_threshold"]
            continue
        scores = [(_f1(y, p, t), t) for t in PREREG["threshold_grid"]]
        best = max(s for s, _ in scores)
        out[f["fold"]] = max(t for s, t in scores if s == best)  # ties -> the higher threshold (fewer alarms)
    return out


# ---------- scoring ------------------------------------------------------------------------------------------------
def score_events(preds: pd.DataFrame, events: pd.DataFrame, thr: np.ndarray,
                 window: int = PREREG["spike_window_days"]) -> tuple[pd.DataFrame, dict]:
    """preds: one model's rows (mandi_id, date, spike_prob); thr: threshold per row. Returns per-event table + totals."""
    alert = preds.assign(alert=preds["spike_prob"].to_numpy() >= thr)
    per_event, false_days, alert_days, mandi_days = [], 0, 0, 0
    for mid, g in alert.groupby("mandi_id"):
        issued = set(pd.DatetimeIndex(g["date"]))
        mandi_days += len(issued)
        alerts = pd.DatetimeIndex(g.loc[g["alert"], "date"])
        alert_days += len(alerts)
        covered = set()
        for e in events[events["mandi_id"] == mid].itertuples():
            win = pd.date_range(e.rise_date - pd.Timedelta(days=window), e.rise_date - pd.Timedelta(days=1), freq="D")
            covered.update(win)
            if not set(win) <= issued:
                continue
            hits = alerts[(alerts >= win[0]) & (alerts <= win[-1])]
            first = hits.min() if len(hits) else None
            per_event.append({"mandi_id": int(mid), "rise_date": e.rise_date, "rise_pct": e.rise_pct,
                              "detected": first is not None,
                              "first_alert": first, "lead_days": (e.rise_date - first).days if first is not None else None})
        false_days += sum(1 for d in alerts if d not in covered)
    t = pd.DataFrame(per_event, columns=["mandi_id", "rise_date", "rise_pct", "detected", "first_alert", "lead_days"])
    n = len(t)
    leads = t.loc[t["detected"], "lead_days"].astype(float)
    years = mandi_days / 365.0
    totals = {"events_scoreable": n, "detected": int(t["detected"].sum()) if n else 0, "forecast_days": mandi_days,
              "mandi_years": round(years, 2), "alert_days": alert_days, "false_alarm_days": false_days}
    if n >= PREREG["min_events"]:
        totals.update({"recall": round(totals["detected"] / n, 3),
                       "lead_days_median": float(leads.median()) if len(leads) else None,
                       "lead_days_min": float(leads.min()) if len(leads) else None,
                       "lead_days_max": float(leads.max()) if len(leads) else None,
                       "alert_day_share_pct": round(100 * alert_days / mandi_days, 1) if mandi_days else None,
                       "alert_precision": round(1 - false_days / alert_days, 3) if alert_days else None,
                       "false_alarm_days_per_mandi_year": round(false_days / years, 1) if years else None,
                       "withheld": False})
    else:
        totals.update({k: None for k in ("recall", "lead_days_median", "lead_days_min", "lead_days_max",
                                         "alert_day_share_pct", "alert_precision", "false_alarm_days_per_mandi_year")})
        totals.update({"withheld": True,
                       "withheld_reason": f"{n} scoreable events < {PREREG['min_events']} (pre-registered minimum)"})
    return t, totals


def evaluate_events(r, events: pd.DataFrame, first_scored: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Both threshold rules for every model in a harness run. Thresholds may learn from every earlier fold; events
    and alarms are scored only on forecasts issued from `first_scored` on. Returns (totals, per-event table)."""
    tot, per = [], []
    for model, g in r.predictions.groupby("model_name", sort=False):
        g = g.reset_index(drop=True)
        by_fold = fold_thresholds(g, r.folds)
        rules = {"fixed_0.5": np.full(len(g), PREREG["fixed_threshold"]),
                 "earlier_folds_f1": g["fold"].map(by_fold).to_numpy(float)}
        keep = (g["date"] >= first_scored).to_numpy() if first_scored is not None else np.ones(len(g), bool)
        for rule, thr in rules.items():
            t, s = score_events(g[keep], events, thr[keep])
            tot.append({"model_name": model, "threshold_rule": rule, **s})
            per.append(t.assign(model_name=model, threshold_rule=rule))
    return pd.DataFrame(tot), (pd.concat(per, ignore_index=True) if per else pd.DataFrame())


def interval_summary(r, first_scored: pd.Timestamp | None = None) -> dict:
    """Pooled pinball / p10-p90 coverage per model x horizon on the scored forecasts, plus the display model's B-1
    calibrated coverage (calibrated from its own earlier forecasts only) and how many rows calibration applied to."""
    from .calibration import apply_to_predictions
    from .eval.metrics import interval_metrics

    out = {}

    def add(name, frame):
        for h in (1, 2, 3, 4):
            c = frame[frame[f"y_h{h}"].notna()]
            base = c["price"].to_numpy()
            y = base * np.exp(c[f"y_h{h}"].to_numpy())
            q = [base * np.exp(c[f"q{x}_h{h}"].to_numpy()) for x in (10, 50, 90)]
            m = interval_metrics(y, *q)
            for k in ("pinball_mean", "coverage_p10_p90_pct", "n"):
                if k in m:
                    out[f"{name}|{h}|{k}"] = m[k]

    for model, g in r.predictions.groupby("model_name", sort=False):
        g = g.reset_index(drop=True)
        sel = g["date"] >= first_scored if first_scored is not None else np.ones(len(g), bool)
        add(model, g[sel])
        if model == DISPLAY:
            cal, _ = apply_to_predictions(g, PREREG["label_lag_days"])
            add(f"{DISPLAY}_calibrated", cal[sel])
            for h in (1, 2, 3, 4):
                out[f"{DISPLAY}_calibrated|{h}|applied_pct"] = round(
                    100 * float((cal.loc[sel, f"calibration_h{h}"] == "applied").mean()), 1)
    return out


def backtest(feat: pd.DataFrame, provenance, spec: FoldSpec, scored_folds: int | None = None) -> dict:
    """scored_folds: score only the last N folds (the earlier ones build the calibration and threshold track record)."""
    r = run(feat, V1_MODELS, feature_set=V1_FEATURE_SET, data_provenance=provenance, spec=spec,
            prepare=add_seasonal_naive, per_mandi=False)
    scored = r.folds[-scored_folds:] if scored_folds else r.folds
    first = pd.Timestamp(scored[0]["cutoff"])
    events = find_events(feat)
    tot, per = evaluate_events(r, events, first)
    return {"data_provenance": r.data_provenance, "folds": r.folds, "scored_from": str(first.date()),
            "data_range": r.data_range, "n_mandis": r.n_mandis, "events_found": int(len(events)),
            "totals": tot, "per_event": per, "events": events, "intervals": interval_summary(r, first)}


# ---------- real ---------------------------------------------------------------------------------------------------
def inventory(prices: pd.DataFrame) -> dict:
    if prices.empty:
        return {"real_price_rows": 0, "mandis": 0, "first_date": None, "last_date": None, "per_mandi": []}
    per = (prices.groupby("mandi_id")["date"].agg(["min", "max", "nunique"]).reset_index()
           .rename(columns={"min": "first", "max": "last", "nunique": "days_with_price"}))
    per["span_days"] = (per["last"] - per["first"]).dt.days + 1
    return {"real_price_rows": int(len(prices)), "mandis": int(len(per)),
            "first_date": str(prices["date"].min().date()), "last_date": str(prices["date"].max().date()),
            "max_span_days": int(per["span_days"].max()),
            "per_mandi": [{"mandi_id": int(x.mandi_id), "first": str(x.first.date()), "last": str(x.last.date()),
                           "days_with_price": int(x.days_with_price), "span_days": int(x.span_days)}
                          for x in per.itertuples()]}


def earliest_first_fold(first: date) -> date:
    """Earliest last-price date that lets make_folds form one fold: > first + min_train + 4 weeks of targets."""
    return first + timedelta(days=PREREG["real_min_train_days"] + 28 + 1)


def run_real(db) -> dict:
    from agripulse_api.config import get_settings

    from .data import load_arrivals, load_prices, load_weather
    from .train import _provenance

    prices = load_prices(db)  # real rows only (source != synthetic)
    inv = inventory(prices)
    first = date.fromisoformat(inv["first_date"]) if inv["first_date"] else SELF_COLLECTION_START
    base = {"prereg": PREREG, "inventory": inv, "earliest_first_fold": str(earliest_first_fold(first)),
            "advanced_models": "TFT / GNN not run: they need this backtest to pass its minimums on real data first."}
    if prices.empty:
        return {"status": "not_enough_real_data", "reason": "no real price rows", **base}
    feat = build_features(prices, load_weather(db), load_arrivals(db), spike_threshold_pct=get_settings().spike_threshold_pct)
    span = int((feat["date"].max() - feat["date"].min()).days)
    spec = FoldSpec(min_train_days=PREREG["real_min_train_days"], step_days=PREREG["step_days"],
                    n_folds=span // PREREG["step_days"] + 1,  # enough folds to reach back to day 365: all history
                    label_lag_days=PREREG["label_lag_days"])
    try:
        out = backtest(feat, _provenance(db, feat, False), spec)
    except NotEnoughHistory as e:
        return {"status": "not_enough_real_data", "reason": str(e), **base}
    return {"status": "ran", **base, **out}


# ---------- synthetic (same rules, V2-0 folds, several draws) -----------------------------------------------------
def run_synthetic(seeds: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    from .eval.baseline import synthetic_features

    tots, ints = [], []
    for sd in seeds:
        feat = synthetic_features(seed=sd)
        # V2-0's final 8 folds are scored; 13 earlier folds (a year) build the track record, exactly as B-1 did.
        out = backtest(feat, SYNTHETIC, FoldSpec(n_folds=8 + 13), scored_folds=8)
        tots.append(out["totals"].assign(seed=sd, events_found=out["events_found"], data_provenance=SYNTHETIC))
        ints.append(pd.DataFrame([{"seed": sd, "key": k, "value": v} for k, v in out["intervals"].items()]))
    return pd.concat(tots, ignore_index=True), pd.concat(ints, ignore_index=True)


def _seeds(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",") if x]


def _jsonable(res: dict) -> dict:
    out = {}
    for k, v in res.items():
        if isinstance(v, pd.DataFrame):
            out[k] = json.loads(v.to_json(orient="records", date_format="iso"))
        else:
            out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance", choices=["real", "synthetic"], default="real")
    ap.add_argument("--seeds", default="1-8")
    a = ap.parse_args(argv)
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    if a.provenance == "synthetic":
        tot, ints = run_synthetic(_seeds(a.seeds))
        tot.to_csv(out / "backtest-events-synthetic.csv", index=False)
        ints.to_csv(out / "backtest-events-synthetic-intervals.csv", index=False)
        print(LABEL[SYNTHETIC])
        print(tot.groupby(["model_name", "threshold_rule"])[["events_scoreable", "recall", "lead_days_median",
                                                              "alert_day_share_pct", "alert_precision",
                                                              "false_alarm_days_per_mandi_year"]].mean().round(3))
        return
    from agripulse_api.db import SessionLocal

    with SessionLocal() as db:
        res = run_real(db)
    (out / "backtest-real.json").write_text(json.dumps(_jsonable(res), indent=2, default=str))
    if res["status"] == "ran":
        res["per_event"].to_csv(out / "backtest-real-events.csv", index=False)
    print(res["status"], "-", res.get("reason", ""), "| provenance:", res.get("data_provenance", "real (no rows scored)"))


if __name__ == "__main__":
    main()
