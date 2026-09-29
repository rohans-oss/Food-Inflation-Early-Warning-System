"""Optional Neo4j mirror of the mandi graph (V2-3).

Models train on the in-memory graph; Neo4j is for exploring it (Cypher, the Neo4j browser). Each push replaces
the previous graph. Relationship types: DISTANCE, PRICE_CORR, FLOW_ESTIMATE; every relationship carries
data_provenance, is_estimate, as_of and build_id, and FLOW_ESTIMATE also carries label = "ESTIMATE".

    MATCH (a:Mandi {name: "Kolar APMC"})-[r:FLOW_ESTIMATE]->(b) RETURN b.name, r.flow_index, r.label

Needs the `graph` extra (`pip install -e .[graph]`) and NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in .env.
"""
import os

import pandas as pd

from .edges import ESTIMATE_LABEL

REL = {"distance": "DISTANCE", "price_corr": "PRICE_CORR", "flow_estimate": "FLOW_ESTIMATE"}


def statements(nodes: pd.DataFrame, edges: pd.DataFrame, as_of, build_id: str) -> list[tuple[str, dict]]:
    """(cypher, params) pairs, in order. Parameterised; no string-built values."""
    out = [("MATCH (:Mandi)-[r:DISTANCE|PRICE_CORR|FLOW_ESTIMATE]->(:Mandi) DELETE r", {}),
           ("CREATE CONSTRAINT mandi_id IF NOT EXISTS FOR (m:Mandi) REQUIRE m.id IS UNIQUE", {})]
    out.append(("UNWIND $rows AS row MERGE (m:Mandi {id: row.id}) SET m.name = row.name, m.lat = row.lat, m.lon = row.lon",
                {"rows": [{"id": int(r.mandi_id), "name": getattr(r, "name", None), "lat": r.lat, "lon": r.lon}
                          for r in nodes.itertuples(index=False)]}))
    for et, rel in REL.items():
        e = edges[edges["edge_type"] == et]
        if e.empty:
            continue
        rows = []
        for r in e.to_dict("records"):
            props = {k: (None if pd.isna(v) else v) for k, v in r.items()
                     if k in ("weight", "km", "distance_source", "correlation", "flow_index", "data_provenance", "is_estimate")}
            props.update(as_of=str(pd.Timestamp(as_of).date()), build_id=build_id,
                         label=ESTIMATE_LABEL if r["is_estimate"] else None)
            rows.append({"src": int(r["src"]), "dst": int(r["dst"]), "props": props})
        out.append((f"UNWIND $rows AS row MATCH (a:Mandi {{id: row.src}}), (b:Mandi {{id: row.dst}}) "
                    f"CREATE (a)-[r:{rel}]->(b) SET r = row.props", {"rows": rows}))
    return out


def push(nodes: pd.DataFrame, edges: pd.DataFrame, as_of, build_id: str, driver=None) -> int:
    """Run the statements; returns how many ran. `driver` is injectable for tests."""
    if driver is None:
        try:
            from neo4j import GraphDatabase
        except ImportError as e:  # pragma: no cover - depends on the optional extra
            raise RuntimeError("Neo4j backend needs `pip install -e .[graph]`") from e
        uri = os.environ.get("NEO4J_URI")
        if not uri:
            raise RuntimeError("[graph] backend = 'neo4j' but NEO4J_URI is not set (.env)")
        driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")))
    stmts = statements(nodes, edges, as_of, build_id)
    with driver.session() as s:
        for q, params in stmts:
            s.run(q, **params)
    return len(stmts)
