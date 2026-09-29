"""Build today's mandi graph from the database and store it (graph_edges; Neo4j too if configured).

    python -m agripulse_ml.graph.build                    # auto: real prices if any mandi has them, else synthetic
    python -m agripulse_ml.graph.build --provenance synthetic

Scheduled weekly (ingest/scheduler.py). The API serves the latest build: GET /graph/mandi/{id}/neighbours.
Distance edges are real either way; correlation / flow edges carry the provenance of the prices they came from,
and flow edges are ESTIMATES.
"""
import argparse
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from agripulse_api.models import GraphEdge, Mandi, Price

from ..features.inputs import Inputs
from .edges import build_graph, graph_config

IST = timezone(timedelta(hours=5, minutes=30))
KEEP_BUILDS = 5


def _has_real_prices(db: Session) -> bool:
    n = db.scalar(select(func.count(func.distinct(Price.mandi_id))).where(Price.source != "synthetic", Price.commodity == "Tomato"))
    return (n or 0) >= 2


def build_and_store(db: Session, provenance: str = "auto", as_of=None, router=None, neo4j_driver=None) -> dict:
    if provenance == "auto":
        provenance = "real" if _has_real_prices(db) else "synthetic"
    synthetic = provenance == "synthetic"
    as_of = pd.Timestamp(as_of or datetime.now(IST).date())
    inputs = Inputs.from_db(db, synthetic=synthetic)
    g = build_graph(inputs, as_of, router=router)
    build_id = uuid.uuid4().hex
    rows = [GraphEdge(build_id=build_id, as_of=as_of.date(), src_mandi_id=int(e["src"]), dst_mandi_id=int(e["dst"]),
                      edge_type=e["edge_type"], weight=float(e["weight"]),
                      km=None if pd.isna(e["km"]) else float(e["km"]),
                      distance_source=None if pd.isna(e["distance_source"]) else e["distance_source"],
                      correlation=None if pd.isna(e["correlation"]) else float(e["correlation"]),
                      flow_index=None if pd.isna(e["flow_index"]) else float(e["flow_index"]),
                      data_provenance=e["data_provenance"], is_estimate=bool(e["is_estimate"]))
            for e in g.edges.to_dict("records")]
    db.add_all(rows)
    db.flush()  # the new build must count among the ones kept
    old = db.scalars(select(GraphEdge.build_id).group_by(GraphEdge.build_id)
                     .order_by(func.max(GraphEdge.created_at).desc()).offset(KEEP_BUILDS)).all()
    if old:
        db.execute(delete(GraphEdge).where(GraphEdge.build_id.in_(old)))
    db.commit()
    out = {"build_id": build_id, "as_of": str(as_of.date()), "prices": provenance, "edges": len(rows),
           "by_type": g.edges.groupby("edge_type").size().to_dict(), "notes": g.notes, "neo4j": None}
    if graph_config()["graph"]["backend"] == "neo4j" or neo4j_driver is not None:
        from .neo4j_store import push

        names = dict(db.execute(select(Mandi.id, Mandi.name)).all())
        nodes = g.nodes.assign(name=g.nodes["mandi_id"].map(names))
        out["neo4j"] = push(nodes, g.edges, as_of, build_id, driver=neo4j_driver)
    return out


def run_job(db: Session) -> dict:
    from ingest.runs import tracked_run

    with tracked_run(db, "graph_build") as run:
        out = build_and_store(db)
        run.rows, run.details = out["edges"], out
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance", choices=["auto", "real", "synthetic"], default="auto")
    args = ap.parse_args()
    from agripulse_api.db import SessionLocal

    with SessionLocal() as db:
        print(build_and_store(db, args.provenance))


if __name__ == "__main__":
    main()
