"""V3-3 rule 23: one live data-status per module, per mandi where it applies, instead of one system-wide
real/synthetic switch. Statuses: real | real_partial | synthetic | not_yet_evaluable (+ counts as evidence)."""
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import readiness
from .modelcfg import display_model
from .models import (Forecast, GraphEdge, LoadProposal, Lot, Mandi, SatelliteObs, ScenarioRun, Trip)

NOT_YET = "not_yet_evaluable"


def _worst(counts: Counter) -> str:
    for s in ("synthetic", "real_partial", "real"):
        if counts.get(s):
            return s
    return NOT_YET


def compute(db: Session) -> list[dict]:
    rd = readiness.compute(db)
    n_mandis = db.scalar(select(func.count(Mandi.id))) or 0
    out = []

    # forecast: provenance of the newest display-model forecast per mandi
    name = display_model()
    newest = db.execute(select(Forecast.mandi_id, func.max(Forecast.issue_date)).where(Forecast.model_name == name)
                        .group_by(Forecast.mandi_id)).all()
    prov, calib = Counter(), Counter()
    for mid, d in newest:
        f = db.scalar(select(Forecast).where(Forecast.mandi_id == mid, Forecast.issue_date == d,
                                             Forecast.model_name == name).limit(1))
        prov[f.data_provenance] += 1
        calib[f.calibration or "none"] += 1
    p = rd["summary"]["prices"]
    out.append({"module": "Price forecast", "status": _worst(prov) if newest else NOT_YET,
                "per_mandi": dict(prov), "mandis": n_mandis,
                "evidence": f"{p['ready']}/{p['total']} mandis have enough REAL price history "
                            f"(ready around {p['latest_projected_ready_date'] or 'unknown'}); calibration: {dict(calib) or 'n/a'}",
                "doc": "docs/v2-summary.md"})

    # mandi graph: distance edges are real; price / flow edges carry the prices' provenance
    build = db.scalar(select(GraphEdge.build_id).order_by(GraphEdge.created_at.desc()).limit(1))
    edges = Counter()
    if build:
        for et, dp in db.execute(select(GraphEdge.edge_type, GraphEdge.data_provenance).where(GraphEdge.build_id == build)):
            edges[f"{et}:{dp}"] += 1
    price_edges = Counter({k.split(":")[1]: v for k, v in edges.items() if not k.startswith("distance")})
    out.append({"module": "Mandi graph", "status": _worst(price_edges) if build else NOT_YET, "per_type": dict(edges),
                "evidence": "distance edges are real; price-correlation and flow edges (ESTIMATES) follow the prices' "
                            "provenance" if build else "no graph build yet (python -m agripulse_ml.graph.build)",
                "doc": "docs/graph-results.md"})

    # satellite: real Sentinel-2, but the crop-signal validation was negative
    n_obs = db.scalar(select(func.count(SatelliteObs.id))) or 0
    districts = db.scalar(select(func.count(func.distinct(SatelliteObs.district)))) or 0
    out.append({"module": "Satellite crop signal", "status": "real" if n_obs else NOT_YET,
                "evidence": f"{n_obs} real Sentinel-2 observations, {districts} districts; validation against tomato "
                            "statistics was NEGATIVE (no signal beyond trend)",
                "doc": "docs/satellite-results.md"})

    # transit: real vs simulated trips; readiness threshold per mandi
    t = rd["summary"]["transit"]
    real_trips = db.scalar(select(func.count(Trip.id)).where(Trip.is_simulated.is_(False), Trip.status == "completed")) or 0
    sim_trips = db.scalar(select(func.count(Trip.id)).where(Trip.is_simulated.is_(True))) or 0
    out.append({"module": "In-transit supply feature", "status": "real_partial" if real_trips else (
        "synthetic" if sim_trips else NOT_YET), "per_mandi": {"ready": t["ready"], "total": t["total"]},
                "evidence": f"{real_trips} completed real trips, {sim_trips} simulated; {t['ready']}/{t['total']} mandis "
                            "past the transit threshold",
                "doc": "docs/ablation-results.md"})

    # decisions: can only be scored with real lots that have a sale outcome
    sold = db.scalar(select(func.count(Lot.id)).where(Lot.is_simulated.is_(False), Lot.sale_price_per_quintal.is_not(None))) or 0
    accepted = db.scalar(select(func.count(LoadProposal.id)).where(LoadProposal.status == "accepted")) or 0
    out.append({"module": "Optimizer, shared + return loads", "status": NOT_YET,
                "evidence": f"{sold} real lots with a sale price; {accepted} accepted load proposals. Study results are "
                            "SYNTHETIC; decisions need real outcomes at chosen AND unchosen mandis",
                "doc": "docs/optimizer-results.md"})

    # scenarios: counterfactual by construction; synthetic while the forecasts are
    runs = db.scalar(select(func.count(ScenarioRun.id))) or 0
    out.append({"module": "Scenario simulator", "status": _worst(prov) if newest else NOT_YET, "counterfactual": True,
                "evidence": f"COUNTERFACTUAL ESTIMATE by design; shifts the forecast above, so it shares its provenance. "
                            f"{runs} runs stored",
                "doc": "docs/scenario-assumptions.md"})
    return out
