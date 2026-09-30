"""Channel A: "what the current model does". Perturb the display model's own raw inputs, rebuild its features, re-run
it, and report the RATIO scenario / baseline per mandi, horizon and quantile.

  rainfall_failure: precipitation x (1 - deficit) for the region's mandis on dates inside the window. The model only
                    sees rain through rain_7, rain_30 and rain_30_anom, i.e. the last 30 days before the issue date.
  export_ban:       arrivals x (1 + export share) from the ban start (the model's arrivals_7_rel sees the last week).

Ratios, not levels: the B-1 calibration offsets are additive in log space and identical for both runs, so they
cancel; the ratio is applied to the stored (calibrated) display forecast. The model was trained on SYNTHETIC data in
which only EXCESS rain drives spikes and arrivals follow price (ml/agripulse_ml/synthetic.py), so its answer here is
a property of that model, not of tomato markets."""
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..data import load_arrivals, load_prices, load_weather
from ..features import HORIZONS, QUANTILES, build_features

MAX_STALENESS_DAYS = 7
MODEL_VIEW_DAYS = {"rainfall_failure": 30, "export_ban": 7}


def _latest(feat: pd.DataFrame) -> pd.DataFrame:
    feat = feat[feat["observed"]]
    latest = feat.sort_values("date").groupby("mandi_id").tail(1)
    return latest[latest["date"] >= latest["date"].max() - timedelta(days=MAX_STALENESS_DAYS)].reset_index(drop=True)


def model_channel(db: Session, scenario: str, params: dict, mandi_ids: set | None, model_dir: str | None = None) -> dict:
    """Returns {"available": bool, "ratios": {mandi_id: {h: {p10, p50, p90}}}, "spike": {...}, "notes": [...]}."""
    import json

    from agripulse_api.config import get_settings

    from ..models import LoadedLightGBM

    d = Path(model_dir or get_settings().model_dir)
    if not (d / "backtest.json").exists():
        return {"available": False, "reason": "No trained display model on this server (python -m agripulse_ml.train).",
                "ratios": {}, "spike": {}, "notes": []}
    synthetic = bool(json.loads((d / "backtest.json").read_text()).get("trained_on_synthetic"))
    model = LoadedLightGBM(d)
    s = get_settings()
    prices = load_prices(db, synthetic=synthetic)
    weather, arrivals = load_weather(db, synthetic=synthetic), load_arrivals(db, synthetic=synthetic)
    if prices.empty:
        return {"available": False, "reason": "No prices.", "ratios": {}, "spike": {}, "notes": []}
    notes = []
    w2, a2 = weather.copy(), arrivals.copy()
    if scenario == "rainfall_failure":
        start, end, deficit = params["start"], params["end"], params["deficit"]
        if len(w2):
            dts = pd.to_datetime(w2["date"]).dt.date
            hit = w2["mandi_id"].isin(mandi_ids or set()) & (dts >= start) & (dts <= end)
            w2.loc[hit, "precip_mm"] = w2.loc[hit, "precip_mm"] * (1 - deficit)
            if not hit.any():
                notes.append("No rainfall rows of the region fall in the window: the model sees no change.")
    elif scenario == "export_ban":
        start, share = params["start"], params["share"]
        if len(a2):
            dts = pd.to_datetime(a2["date"]).dt.date
            hit = dts >= start
            a2.loc[hit, "tonnes"] = a2.loc[hit, "tonnes"] * (1 + share)
            if not hit.any():
                notes.append("The ban starts after the latest data: the model sees no change yet.")
    base = _latest(build_features(prices, weather, arrivals, spike_threshold_pct=s.spike_threshold_pct, with_targets=False))
    pert = _latest(build_features(prices, w2, a2, spike_threshold_pct=s.spike_threshold_pct, with_targets=False))
    issue = base["date"].max().date()
    view = MODEL_VIEW_DAYS[scenario]
    first = params["start"]
    last = params.get("end", issue)
    if last < issue - timedelta(days=view) or first > issue:
        notes.append(f"The scenario window is outside the model's {view}-day view before {issue}: no change is expected.")
    pb, pp = model.predict(base), model.predict(pert)
    ratios, spike = {}, {}
    for i, mid in enumerate(base["mandi_id"].astype(int)):
        j = int(np.flatnonzero(pert["mandi_id"].astype(int).to_numpy() == mid)[0])
        ratios[mid] = {h: {f"p{int(q * 100)}": float(np.exp(pp[(h, f'p{int(q * 100)}')][j] - pb[(h, f'p{int(q * 100)}')][i]))
                           for q in QUANTILES} for h in HORIZONS}
        spike[mid] = {"baseline": float(pb["spike_prob"][i]), "scenario": float(pp["spike_prob"][j])}
    return {"available": True, "ratios": ratios, "spike": spike, "notes": notes, "issue_date": str(issue),
            "trained_on_synthetic": synthetic}
