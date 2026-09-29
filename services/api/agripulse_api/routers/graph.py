"""Mandi graph (V2-3): which mandis move together, how far apart they are, where produce is ESTIMATED to flow.

Serves the latest `python -m agripulse_ml.graph.build` output from graph_edges. Every edge says what it is:
distance edges are real road km (or a flagged straight-line fallback); correlation edges come from price
history and carry its provenance; flow edges are ESTIMATES (relative index, never tonnes).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GraphEdge, Mandi
from ..provenance import LABEL, worst
from ..rbac import require

router = APIRouter(prefix="/graph", tags=["mandi graph"])

ESTIMATE_LABEL = "ESTIMATE — relative trade-flow index from an arbitrage-gravity rule, not measured tonnes"
EDGE_LABEL = {
    "distance": "Road distance",
    "price_corr": "Prices move together",
    "flow_estimate": "Estimated trade flow",
}


def latest_build(db: Session) -> tuple[str, object, object] | None:
    row = db.execute(select(GraphEdge.build_id, GraphEdge.as_of, func.max(GraphEdge.created_at).label("built_at"))
                     .group_by(GraphEdge.build_id, GraphEdge.as_of).order_by(func.max(GraphEdge.created_at).desc()).limit(1)).first()
    return (row.build_id, row.as_of, row.built_at) if row else None


def _edge_out(e: GraphEdge, other: Mandi, direction: str) -> dict:
    return {
        "mandi": {"id": other.id, "name": other.name, "district": other.district},
        "edge_type": e.edge_type,
        "edge_label": EDGE_LABEL[e.edge_type],
        "direction": direction,  # both (distance / correlation) | in (produce flows here) | out (flows from here)
        "weight": round(e.weight, 4),
        "km": e.km,
        "distance_source": e.distance_source,
        "correlation": None if e.correlation is None else round(e.correlation, 3),
        "flow_index": None if e.flow_index is None else round(e.flow_index, 3),
        "data_provenance": e.data_provenance,
        "provenance_label": LABEL[e.data_provenance],
        "is_estimate": e.is_estimate,
        "estimate_label": ESTIMATE_LABEL if e.is_estimate else None,
    }


@router.get("/mandi/{mandi_id}/neighbours")
def neighbours(mandi_id: int, db: Session = Depends(get_db), _=Depends(require("graph:read"))):
    m = db.get(Mandi, mandi_id)
    if m is None:
        raise HTTPException(404, "Mandi not found")
    build = latest_build(db)
    base = {"mandi": {"id": m.id, "name": m.name, "district": m.district}}
    if build is None:
        return {**base, "build": None, "neighbours": [], "data_provenance": None,
                "notes": ["The mandi graph has not been built yet: python -m agripulse_ml.graph.build"]}
    build_id, as_of, created = build
    edges = db.scalars(select(GraphEdge).where(GraphEdge.build_id == build_id,
                                              (GraphEdge.src_mandi_id == mandi_id) | (GraphEdge.dst_mandi_id == mandi_id))).all()
    names = {x.id: x for x in db.scalars(select(Mandi))}
    out = []
    for e in edges:
        if e.edge_type == "flow_estimate":
            other, direction = (e.dst_mandi_id, "out") if e.src_mandi_id == mandi_id else (e.src_mandi_id, "in")
        elif e.src_mandi_id == mandi_id:  # symmetric types are stored both ways; take one
            other, direction = e.dst_mandi_id, "both"
        else:
            continue
        out.append(_edge_out(e, names[other], direction))
    order = {"distance": 0, "price_corr": 1, "flow_estimate": 2}
    out.sort(key=lambda x: (order[x["edge_type"]], -x["weight"]))
    prov = worst(*(x["data_provenance"] for x in out)) if out else None
    notes = [f"Built {as_of} from data published before that date."]
    if any(x["distance_source"] == "haversine" for x in out):
        notes.append("Distances are straight-line x 1.3 (routing server not reachable); mandi coordinates are approximate.")
    if any(x["is_estimate"] for x in out):
        notes.append(ESTIMATE_LABEL)
    return {**base, "build": {"build_id": build_id, "as_of": as_of, "built_at": created}, "neighbours": out,
            "data_provenance": prov, "provenance_label": LABEL[prov] if prov else None, "notes": notes}
