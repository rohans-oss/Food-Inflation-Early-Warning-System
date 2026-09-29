"""Mandi graph (V2-3): nodes = mandis, edges = distance (REAL), price correlation, trade-flow ESTIMATE.

    g = build_graph(inputs, as_of=pd.Timestamp("2026-05-09"))
    g.edges        DataFrame: src, dst, edge_type, weight, km, distance_source, correlation, flow_index,
                   data_provenance, is_estimate
    g.to_networkx()

Time rule (V2 rule 12): a graph "as of" S is usable at any issue date t >= S. Its price / arrival edges use only
values published by S (value dated d with d + lag <= S). Distance edges don't depend on time.
The in-memory graph (networkx / DataFrame) is what models train on; Neo4j (graph/neo4j_store.py) is an optional
mirror for exploration, fed from the same edges.
"""
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from agripulse_api.provenance import REAL, worst

from ..features.config import lag

_DEFAULT = Path(__file__).resolve().parents[3] / "config" / "graph.toml"
EDGE_COLUMNS = ["src", "dst", "edge_type", "weight", "km", "distance_source", "correlation", "flow_index",
                "data_provenance", "is_estimate"]
EDGE_TYPES = ("distance", "price_corr", "flow_estimate")
ESTIMATE_LABEL = "ESTIMATE: relative trade-flow index from an arbitrage-gravity rule, not measured tonnes"


@lru_cache
def graph_config() -> dict:
    path = Path(os.environ.get("GRAPH_CONFIG", _DEFAULT))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


@dataclass
class MandiGraph:
    as_of: pd.Timestamp
    nodes: pd.DataFrame  # mandi_id, lat, lon
    edges: pd.DataFrame  # EDGE_COLUMNS
    notes: list[str] = field(default_factory=list)

    def of_type(self, edge_type: str) -> pd.DataFrame:
        return self.edges[self.edges["edge_type"] == edge_type]

    def matrix(self, edge_type: str, ids: list[int], value: str = "weight") -> np.ndarray:
        """Dense [dst, src] matrix: row = the receiving mandi, column = the neighbour it listens to.
        Distance / correlation edges are stored in both directions; flow edges src -> dst (produce moves to dst)."""
        pos = {m: i for i, m in enumerate(ids)}
        W = np.zeros((len(ids), len(ids)))
        for s, d, v in self.of_type(edge_type)[["src", "dst", value]].itertuples(index=False):
            if s in pos and d in pos:
                W[pos[d], pos[s]] = v
        return W

    def to_networkx(self):
        import networkx as nx

        G = nx.MultiDiGraph(as_of=str(self.as_of.date()))
        for r in self.nodes.itertuples(index=False):
            G.add_node(int(r.mandi_id), lat=r.lat, lon=r.lon)
        for e in self.edges.to_dict("records"):
            G.add_edge(int(e["src"]), int(e["dst"]), key=e["edge_type"],
                       **{k: v for k, v in e.items() if k not in ("src", "dst")})
        return G


# ---------------------------------------------------------------- distance (real, time-independent)

_KM_CACHE: dict = {}


def pair_km(nodes: pd.DataFrame, router=None) -> pd.DataFrame:
    """All ordered pairs with road km. `router(o_lat, o_lon, d_lat, d_lon) -> (km, minutes, source)`;
    default is tracking.routing.road_km (OSRM, or the labelled straight-line fallback)."""
    if router is None:
        from tracking.routing import road_km as router
    nodes = nodes.dropna(subset=["lat", "lon"])
    out = []
    recs = nodes[["mandi_id", "lat", "lon"]].to_dict("records")
    for a in recs:
        for b in recs:
            if a["mandi_id"] >= b["mandi_id"]:
                continue
            key = (round(a["lat"], 5), round(a["lon"], 5), round(b["lat"], 5), round(b["lon"], 5), router)
            if key not in _KM_CACHE:
                km, _, src = router(a["lat"], a["lon"], b["lat"], b["lon"])
                _KM_CACHE[key] = (float(km), src)
            km, src = _KM_CACHE[key]
            out += [(int(a["mandi_id"]), int(b["mandi_id"]), km, src), (int(b["mandi_id"]), int(a["mandi_id"]), km, src)]
    return pd.DataFrame(out, columns=["src", "dst", "km", "distance_source"])


def distance_edges(km: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = cfg["distance"]
    e = km[km["km"] <= float(c["radius_km"])].sort_values(["dst", "km"])
    e = e.groupby("dst").head(int(c["max_neighbours"]))  # each mandi listens to its N nearest
    both = pd.concat([e, e.rename(columns={"src": "dst", "dst": "src"})]).drop_duplicates(["src", "dst"])
    both = both.assign(edge_type="distance", weight=np.exp(-both["km"] / float(c["decay_km"])), correlation=np.nan,
                       flow_index=np.nan, data_provenance=REAL, is_estimate=False)
    return both[EDGE_COLUMNS]


# ---------------------------------------------------------------- time-dependent edges

def _published(df: pd.DataFrame, as_of: pd.Timestamp, source: str, window_days: int) -> pd.DataFrame:
    """Values dated d with d + lag(source) <= as_of, within the last `window_days` days of what is published."""
    last = as_of - pd.Timedelta(days=lag(source))
    return df[(df["date"] <= last) & (df["date"] > last - pd.Timedelta(days=window_days))]


def correlation_edges(prices: pd.DataFrame, as_of: pd.Timestamp, km: pd.DataFrame, price_prov: dict, cfg: dict) -> pd.DataFrame:
    c = cfg["correlation"]
    p = _published(prices, as_of, "prices", int(c["window_days"]))
    if p.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    grid = p.pivot_table(index="date", columns="mandi_id", values="price")
    grid = grid.reindex(pd.date_range(grid.index.min(), grid.index.max(), freq="D")).ffill(limit=3)
    chg = np.log(grid).diff(int(c["change_days"]))
    corr = chg.corr(min_periods=int(c["min_overlap"]))
    rows = []
    for d in corr.columns:
        cand = corr[d].drop(d).dropna()
        cand = cand[cand >= float(c["min_corr"])].sort_values(ascending=False).head(int(c["max_neighbours"]))
        rows += [(int(s), int(d), float(v)) for s, v in cand.items()]
    e = pd.DataFrame(rows, columns=["src", "dst", "correlation"])
    if e.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    both = pd.concat([e, e.rename(columns={"src": "dst", "dst": "src"})]).drop_duplicates(["src", "dst"])
    both = both.merge(km, on=["src", "dst"], how="left")
    both["edge_type"], both["weight"], both["flow_index"], both["is_estimate"] = "price_corr", both["correlation"], np.nan, False
    both["data_provenance"] = [worst(price_prov.get(s, REAL), price_prov.get(d, REAL)) for s, d in zip(both["src"], both["dst"])]
    return both[EDGE_COLUMNS]


def flow_edges(prices: pd.DataFrame, arrivals: pd.DataFrame, as_of: pd.Timestamp, km: pd.DataFrame,
               price_prov: dict, cfg: dict) -> pd.DataFrame:
    """ESTIMATE (rule 13): see config/graph.toml [flow_estimate]."""
    c = cfg["flow_estimate"]
    w = int(c["window_days"])
    p = _published(prices, as_of, "prices", w).groupby("mandi_id")["price"].mean()
    a = _published(arrivals, as_of, "arrivals", w).groupby("mandi_id")["tonnes"].mean() if len(arrivals) else pd.Series(dtype=float)
    if p.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    e = km[km["src"].isin(p.index) & km["dst"].isin(p.index) & (km["km"] <= float(cfg["distance"]["radius_km"]))].copy()
    e["margin"] = e["dst"].map(p).to_numpy() - e["src"].map(p).to_numpy() - float(c["rate_per_quintal_km"]) * e["km"]
    e["supply"] = e["src"].map(a).fillna(1.0) if len(a) else 1.0
    e = e[e["margin"] > 0]
    if e.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS)
    e["raw"] = e["supply"] * e["margin"] / e["km"].clip(lower=1)
    e = e.sort_values("raw", ascending=False).groupby("src").head(int(c["max_neighbours"]))
    e["flow_index"] = e["raw"] / e["raw"].max()
    e["edge_type"], e["weight"], e["correlation"], e["is_estimate"] = "flow_estimate", e["flow_index"], np.nan, True
    e["data_provenance"] = [worst(price_prov.get(s, REAL), price_prov.get(d, REAL)) for s, d in zip(e["src"], e["dst"])]
    return e[EDGE_COLUMNS]


def build_graph(inputs, as_of, cfg: dict | None = None, km: pd.DataFrame | None = None, router=None) -> MandiGraph:
    cfg = cfg or graph_config()
    as_of = pd.Timestamp(as_of)
    nodes = inputs.mandis[["mandi_id", "lat", "lon"]].copy()
    km = pair_km(nodes, router) if km is None else km
    parts = [distance_edges(km, cfg),
             correlation_edges(inputs.prices, as_of, km, inputs.price_provenance, cfg),
             flow_edges(inputs.prices, inputs.arrivals, as_of, km, inputs.price_provenance, cfg)]
    edges = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) \
        else pd.DataFrame(columns=EDGE_COLUMNS)
    edges[["src", "dst"]] = edges[["src", "dst"]].astype(int)
    notes = []
    if len(km) and (km["distance_source"] != "osrm").any():
        notes.append("distance edges use straight line x road factor (OSRM not reachable); coordinates are approximate")
    notes.append(ESTIMATE_LABEL)
    return MandiGraph(as_of=as_of, nodes=nodes, edges=edges, notes=notes)


def snapshot_dates(start: pd.Timestamp, end: pd.Timestamp, every_days: int | None = None) -> pd.DatetimeIndex:
    every = int(every_days or graph_config()["graph"]["snapshot_every_days"])
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=f"{every}D")
