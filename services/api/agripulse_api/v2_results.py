"""V2 (Intelligence) findings shown on the Admin page. Each one carries its data provenance; the numbers live in the
linked docs, which are generated from docs/results/*.csv. Update this list when a result is re-run on real data."""
from .provenance import LABEL, REAL, SYNTHETIC

RESULTS = [
    {"phase": "V2-0", "title": "Shared evaluation harness", "outcome": "finding",
     "headline": "V1's 'LightGBM beats naive' held on one synthetic draw only; across 8 draws there is no reliable edge.",
     "data_provenance": SYNTHETIC, "doc": "docs/backtest-synthetic.md"},
    {"phase": "V2-2", "title": "Temporal Fusion Transformer", "outcome": "negative",
     "headline": "15-33% worse than naive on pinball across 3 draws; raw intervals hold the price 46-59% of the time.",
     "data_provenance": SYNTHETIC, "doc": "docs/tft-results.md"},
    {"phase": "V2-3", "title": "Mandi graph + GNN", "outcome": "mixed",
     "headline": "Graph features help LightGBM 1-3% on every draw but only to parity with naive; the GNN loses 10-32%.",
     "data_provenance": SYNTHETIC, "doc": "docs/graph-results.md"},
    {"phase": "V2-4", "title": "Sentinel-2 crop signal vs tomato statistics", "outcome": "negative",
     "headline": "No evidence district cropland NDVI tracks tomato area or production beyond a shared trend "
                 "(r 0.60 -> -0.14 detrended, 2 districts, n = 10). The NDVI signal itself is sound.",
     "data_provenance": REAL, "doc": "docs/satellite-results.md"},
    {"phase": "V2-5", "title": "Ablation of feature groups", "outcome": "finding",
     "headline": "No group makes LightGBM reliably beat naive. Graph +1-3%, transit gain is built in (simulated), "
                 "satellite gain is seasonality; removing weather helped in 2 of 3 draws.",
     "data_provenance": SYNTHETIC, "doc": "docs/ablation-results.md"},
    {"phase": "V2-5", "title": "Real-data ablation", "outcome": "not enough real data",
     "headline": "Real prices start 2026-09-25; the harness needs about 13 months. Same command re-runs it then.",
     "data_provenance": REAL, "doc": "docs/ablation-results.md"},
    {"phase": "B-1", "title": "Forecast range calibration (V1 LightGBM, 8 V2-0 datasets)", "outcome": "mixed",
     "headline": "Mean p10-p90 coverage 79/78/77/74% -> 81/80/79/79% (1-4 wk, target 80). The average is fixed; "
                 "per-dataset spread is not (3-5 of 8 datasets within +-5 pts per horizon after, 1-7 before).",
     "data_provenance": SYNTHETIC, "doc": "docs/calibration-results.md", "calibration": "applied"},
    {"phase": "V3-0", "title": "OR-Tools optimizer vs V1 rule (240 simulated batches)", "outcome": "mixed",
     "headline": "Net value a tie (+0.2%), mandi-room violations 140 -> 0, transport cost -22 to -34%. The gain is truck "
                 "assignment; in dense batches it earns 1.1% less by not flooding mandis. Now the default recommender.",
     "data_provenance": SYNTHETIC, "doc": "docs/optimizer-results.md"},
    {"phase": "V3-1", "title": "Shared truckloads + return loads (240 simulated batches)", "outcome": "finding",
     "headline": "Better than V3-0 on 204/240 days, worse on 2, 0 violations: +2 to +13% in sparse/medium batches. Dense "
                 "+38-47% is mostly shipping twice the lots (unshipped lots scored at 0); per tonne: transport -11 to -18%.",
     "data_provenance": SYNTHETIC, "doc": "docs/optimizer-results.md"},
    {"phase": "V3-2", "title": "Scenario simulator (COUNTERFACTUAL ESTIMATE)", "outcome": "finding",
     "headline": "Assumption chain (sourced elasticity -0.72, FAO ky): 50% Jun-Jul rain deficit -> about +10% (range "
                 "+0.6 to +49%); export ban -> -0.3% (exports are 0.47% of output). The synthetic-trained model gives "
                 "wrong-sign or erratic answers for both: shown separately, never blended.",
     "data_provenance": SYNTHETIC, "doc": "docs/scenario-assumptions.md"},
    {"phase": "V3-0", "title": "Decisions on real data", "outcome": "not enough real data",
     "headline": "No real lots with a known sale outcome yet; decisions can't be scored on real data until the field "
                 "pilot and ~13 months of real prices.",
     "data_provenance": REAL, "doc": "docs/optimizer-results.md"},
]


def live_calibration(db) -> dict:
    """Pre-V3 B-1: calibration status of the displayed model's newest forecasts (what users see right now)."""
    from sqlalchemy import func, select

    from .modelcfg import display_model
    from .models import Forecast

    name = display_model()
    newest = db.scalar(select(func.max(Forecast.issue_date)).where(Forecast.model_name == name))
    if newest is None:
        kinds, prov = set(), REAL
    else:
        rows = db.execute(select(Forecast.calibration, Forecast.trained_on_synthetic)
                          .where(Forecast.model_name == name, Forecast.issue_date == newest)).all()
        kinds = {r[0] or "none" for r in rows}
        prov = SYNTHETIC if any(r[1] for r in rows) else REAL
    status = kinds.pop() if len(kinds) == 1 else ("partial" if kinds else "none")
    text = {"applied": "applied: ranges widened from this model's own track record",
            "not_yet_applicable": "not yet applicable: fewer than min_scores published outcomes in the window",
            "partial": "applied for some horizons only", "none": "none (no forecasts yet, or calibration switched off)"}
    return {"phase": "B-1", "title": f"Live calibration of displayed ranges ({name})", "outcome": "status",
            "headline": f"Newest forecasts ({newest or 'none'}): calibration {text.get(status, status)}.",
            "data_provenance": prov, "doc": "docs/calibration-results.md", "calibration": status}


def v2_results(db=None) -> list[dict]:
    rows = RESULTS + ([live_calibration(db)] if db is not None else [])
    return [{**r, "provenance_label": LABEL[r["data_provenance"]]} for r in rows]
