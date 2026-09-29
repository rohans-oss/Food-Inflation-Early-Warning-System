"""V2-3 mandi graph: GNN leakage and isolation, positive-control generator, build + API + labels, Neo4j mirror,
config-only switch to real data. The GNN here is tiny (2 epochs): only the plumbing is tested."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agripulse_api.models import GraphEdge, Mandi, Price
from agripulse_api.provenance import REAL, REAL_PARTIAL, SYNTHETIC
from agripulse_ml.features.inputs import Inputs
from agripulse_ml.features.store import build_table
from agripulse_ml.graph.build import build_and_store
from agripulse_ml.graph.experiment import run_experiment
from agripulse_ml.graph.gnn import GraphFitCache, GraphForecaster
from agripulse_ml.synthetic import SYNTH_MANDIS, generate, load_into_db, propagation_lag_days

TINY = {"data_provenance": "synthetic", "feature_set": "prices+weather+graph", "synthetic_seeds": [7],
        "generators": ["random"], "n_folds": 1, "seq_len": 14, "hidden": 8,
        "learning_rate": 0.003, "weight_decay": 0.0, "batch_size": 32, "max_epochs": 2, "early_stopping_patience": 1,
        "validation_days": 30, "calibration_days": 60, "spike_loss_weight": 1.0, "seed": 1}


@pytest.fixture(scope="module")
def table():
    return build_table(Inputs.from_synthetic(seed=5, start=date(2024, 1, 1), end=date(2025, 3, 31), n_mandis=5),
                       "prices+weather+graph")


@pytest.fixture(scope="module")
def fitted(table):
    pytest.importorskip("torch")
    cutoff = pd.Timestamp("2025-02-01")
    df = table.df
    history = df[df["date"] < cutoff]
    train = history[history["target_date_h4"] + pd.Timedelta(days=table.label_lag_days) < cutoff]
    cache = GraphFitCache()
    gnn = GraphForecaster(TINY, table, use_graph=True, cache=cache).fit(train, history=history)
    ctl = GraphForecaster(TINY, table, use_graph=False, cache=cache).fit(train, history=history)
    return gnn, ctl, cutoff


# ---------------------------------------------------------------- GNN


def test_gnn_ignores_every_mandis_rows_after_the_issue_date(fitted, table):
    gnn, _, cutoff = fitted
    t = cutoff + pd.Timedelta(days=5)
    rows = table.df[table.df["date"] == t]
    ctx = table.df[table.df["date"] < cutoff + pd.Timedelta(days=28)]
    before = gnn.predict(rows, context=ctx)
    tampered = ctx.copy()
    after = tampered["date"] > t
    num = [c for c in tampered.columns if tampered[c].dtype.kind == "f" and not c.startswith("target")]
    tampered.loc[after, num] = tampered.loc[after, num] * 3 + 7
    again = gnn.predict(rows, context=tampered)
    for k in before:
        np.testing.assert_allclose(before[k], again[k], rtol=1e-5, atol=1e-6)


def test_messages_flow_only_in_the_graph_model(fitted, table):
    """Change ONE mandi's past: the GNN forecast of its neighbours moves; the no-graph control's doesn't."""
    gnn, ctl, cutoff = fitted
    t = cutoff + pd.Timedelta(days=5)
    rows = table.df[table.df["date"] == t]
    ctx = table.df[table.df["date"] <= t]
    src = int(rows["mandi_id"].iloc[0])
    others = rows["mandi_id"] != src
    changed = ctx.copy()
    sel = (changed["mandi_id"] == src) & (changed["date"] > t - pd.Timedelta(days=10))
    changed.loc[sel, ["px_chg_1", "px_chg_7", "px_chg_14"]] += 1.0
    for model, should_move in ((gnn, True), (ctl, False)):
        a = model.predict(rows, context=ctx)[(1, "p50")][others.to_numpy()]
        b = model.predict(rows, context=changed)[(1, "p50")][others.to_numpy()]
        assert (not np.allclose(a, b, atol=1e-7)) == should_move, model.name


def test_gnn_uses_the_graph_snapshot_from_before_the_cutoff(fitted, table):
    gnn, ctl, cutoff = fitted
    used = [g for g in table.graphs if g.as_of <= cutoff][-1]
    assert used.as_of <= cutoff and gnn.state["n_edges"] > 0
    assert ctl.state["n_edges"] == 0 and np.allclose(ctl.state["A"].numpy(), np.eye(len(ctl.ids)))
    rows = table.df[(table.df["date"] >= cutoff) & (table.df["date"] < cutoff + pd.Timedelta(days=7))]
    p = gnn.predict(rows, context=table.df[table.df["date"] < cutoff + pd.Timedelta(days=7)])
    for h in (1, 2, 3, 4):
        assert (p[(h, "p10")] <= p[(h, "p50")] + 1e-6).all() and (p[(h, "p50")] <= p[(h, "p90")] + 1e-6).all()
    assert ((p["spike_prob"] >= 0) & (p["spike_prob"] <= 1)).all()


# ---------------------------------------------------------------- generator: positive control


def test_distance_generator_keeps_the_random_stream_and_orders_spikes_by_distance():
    a = generate(date(2023, 1, 1), date(2023, 12, 31), 4, SYNTH_MANDIS)
    b = generate(date(2023, 1, 1), date(2023, 12, 31), 4, SYNTH_MANDIS, propagation="random")
    c = generate(date(2023, 1, 1), date(2023, 12, 31), 4, SYNTH_MANDIS, propagation="distance")
    assert a[0].equals(b[0]) and a[1].equals(c[1])  # default unchanged; weather stream identical in both modes
    lags = {m[0]: propagation_lag_days(m[2], m[3]) for m in SYNTH_MANDIS}
    assert lags["Kolar APMC"] == 0 and lags["Belgaum APMC"] > lags["Mysuru APMC"] > lags["Bangarpet APMC"]
    with pytest.raises(ValueError):
        generate(date(2023, 1, 1), date(2023, 2, 1), 4, SYNTH_MANDIS, propagation="teleport")


# ---------------------------------------------------------------- build + API


@pytest.fixture()
def built(db):
    load_into_db(db, start=date(2025, 6, 1), end=date(2026, 9, 20), seed=3)
    return build_and_store(db, as_of="2026-09-21")


def test_build_stores_labelled_edges_and_keeps_five_builds(db, built):
    assert built["prices"] == "synthetic" and built["by_type"]["distance"] > 0 and built["by_type"]["price_corr"] > 0
    e = db.query(GraphEdge).filter(GraphEdge.build_id == built["build_id"]).all()
    assert all(x.data_provenance == REAL for x in e if x.edge_type == "distance")
    assert all(x.data_provenance == SYNTHETIC for x in e if x.edge_type != "distance")
    assert all(x.is_estimate == (x.edge_type == "flow_estimate") for x in e)
    assert all(x.distance_source == "haversine" for x in e if x.edge_type == "distance")  # no OSRM in tests
    for _ in range(5):
        build_and_store(db, as_of="2026-09-21")
    assert len({b for (b,) in db.query(GraphEdge.build_id).distinct()}) == 5


def test_neighbours_api(client, as_role, db, built):
    kolar = db.query(Mandi).filter(Mandi.name == "Kolar APMC").one()
    r = client.get(f"/graph/mandi/{kolar.id}/neighbours", headers=as_role("policy"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["build"]["build_id"] == built["build_id"] and body["data_provenance"] == SYNTHETIC
    assert body["provenance_label"].startswith("SYNTHETIC")
    types = {n["edge_type"] for n in body["neighbours"]}
    assert {"distance", "price_corr"} <= types
    for n in body["neighbours"]:
        assert n["mandi"]["id"] != kolar.id
        if n["edge_type"] == "flow_estimate":
            assert n["is_estimate"] and n["estimate_label"].startswith("ESTIMATE") and n["direction"] in ("in", "out")
        else:
            assert not n["is_estimate"] and n["direction"] == "both"
        if n["edge_type"] == "distance":
            assert n["data_provenance"] == REAL and n["km"] > 0
    assert any("straight-line" in x for x in body["notes"])
    assert client.get(f"/graph/mandi/{kolar.id}/neighbours", headers=as_role("farmer")).status_code == 403
    assert client.get("/graph/mandi/99999/neighbours", headers=as_role("admin")).status_code == 404


def test_neighbours_before_any_build(client, as_role, db):
    m = db.query(Mandi).first()
    body = client.get(f"/graph/mandi/{m.id}/neighbours", headers=as_role("trader")).json()
    assert body["build"] is None and body["neighbours"] == [] and "not been built" in body["notes"][0]


# ---------------------------------------------------------------- Neo4j mirror


class _FakeSession:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **params):
        self.log.append((q, params))


class _FakeDriver:
    def __init__(self):
        self.log = []

    def session(self):
        return _FakeSession(self.log)


def test_neo4j_mirror_statements(db, built):
    drv = _FakeDriver()
    out = build_and_store(db, as_of="2026-09-21", neo4j_driver=drv)
    qs = [q for q, _ in drv.log]
    assert out["neo4j"] == len(drv.log) and qs[0].startswith("MATCH") and "DELETE r" in qs[0]
    flow = [(q, p) for q, p in drv.log if ":FLOW_ESTIMATE]" in q]
    assert flow and all(r["props"]["is_estimate"] and r["props"]["label"].startswith("ESTIMATE")
                        for r in flow[0][1]["rows"])
    dist = [(q, p) for q, p in drv.log if ":DISTANCE]" in q][0]
    assert all(r["props"]["data_provenance"] == REAL and r["props"]["label"] is None for r in dist[1]["rows"])
    assert all("$rows" in q for q in qs[2:])  # parameterised, values never spliced into Cypher
    nodes = [p for q, p in drv.log if q.startswith("UNWIND $rows AS row MERGE (m:Mandi")][0]["rows"]
    assert any(n["name"] == "Kolar APMC" for n in nodes)


# ---------------------------------------------------------------- real-data switch (config only)


def test_config_switch_to_real_needs_no_code_change(db):
    pytest.importorskip("torch")
    mids = [m.id for m in db.query(Mandi).order_by(Mandi.id).limit(3)]
    today, rng = date.today(), np.random.default_rng(3)
    for mid, days in zip(mids, (500, 500, 250)):  # the third mandi has < 365 days: real_partial
        p = 1500.0
        for k in range(days, 0, -1):
            p = max(300.0, p * float(np.exp(rng.normal(0, 0.04))))
            db.add(Price(mandi_id=mid, commodity="Tomato", date=today - timedelta(days=k), modal_price=round(p),
                         min_price=round(p * 0.8), max_price=round(p * 1.2), source="agmarknet"))
    db.commit()
    out = run_experiment(dict(TINY, data_provenance="real"), db=db, n_folds=1, fold_overrides={"min_train_days": 300})
    res = out["results"]
    assert set(res["model_name"]) == {"seasonal_naive", "naive", "lightgbm_v1", "lightgbm_graph", "gru_nograph", "gnn"}
    # graph features mix in neighbours' prices, so a mandi is only as "real" as the thinnest mandi it listens to:
    # the 250-day mandi makes every mandi real_partial here (worst-of rule), never synthetic
    assert set(res["data_provenance"]) == {REAL_PARTIAL} and set(res["generator"]) == {"real"}
    assert out["data_provenance"] == REAL_PARTIAL
