"""`graph` feature group (V2-3): what a mandi's neighbours are doing, as of each issue date.

Every issue date t uses the graph snapshot S(t) = latest snapshot date <= t (snapshots every
`snapshot_every_days`, anchored on the table's first date), and each snapshot is built only from values published
by S (graph/edges.py). Neighbour values are the neighbours' own past-only price features at the same t, which
are already publication-lag safe. So a graph feature at t never uses anything published after t.

Columns (all past-only):
  gr_dist_chg_7 / _14   distance-weighted mean of neighbours' 7 / 14-day log price change
  gr_dist_spread        own log price minus distance-weighted mean neighbour log price
  gr_dist_risen         distance-weighted share of neighbours >= 30% above their 28-day low
  gr_corr_chg_7         correlation-weighted mean of neighbours' 7-day change
  gr_flow_up_chg_7      flow-ESTIMATE-weighted mean 7-day change of mandis whose produce flows here (estimate)
  gr_n_corr             number of correlation edges into this mandi (how much the graph knows)
"""
import numpy as np
import pandas as pd

from .edges import build_graph, graph_config, pair_km, snapshot_dates

GRAPH = ["gr_dist_chg_7", "gr_dist_chg_14", "gr_dist_spread", "gr_dist_risen", "gr_corr_chg_7",
         "gr_flow_up_chg_7", "gr_n_corr"]
RISEN_LOG = np.log(1.3)


def _wmean(V: pd.DataFrame, W: np.ndarray) -> pd.DataFrame:
    """Row t, mandi i: sum_j W[i, j] V[t, j] / sum_j W[i, j] over neighbours j with a value at t."""
    have = V.notna().to_numpy().astype(float)
    num = np.nan_to_num(V.to_numpy()) @ W.T
    den = have @ W.T
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    return pd.DataFrame(out, index=V.index, columns=V.columns)


def graph_features(df: pd.DataFrame, inputs, cfg: dict | None = None, router=None) -> tuple[pd.DataFrame, list]:
    """df: the feature table so far (mandi_id, date, price, px_chg_7, px_chg_14, px_rel_min_28).
    Returns (frame with mandi_id, date + GRAPH columns, list of MandiGraph snapshots)."""
    cfg = cfg or graph_config()
    ids = sorted(int(m) for m in df["mandi_id"].unique())
    dates = pd.DatetimeIndex(sorted(df["date"].unique()))
    piv = {c: df.pivot_table(index="date", columns="mandi_id", values=c, dropna=False).reindex(index=dates, columns=ids)
           for c in ("px_chg_7", "px_chg_14", "price", "px_rel_min_28")}
    logp = np.log(piv["price"])
    risen = (piv["px_rel_min_28"] >= RISEN_LOG).astype(float).where(piv["px_rel_min_28"].notna())
    km = pair_km(inputs.mandis[["mandi_id", "lat", "lon"]], router)
    snaps = snapshot_dates(dates.min(), dates.max(), cfg["graph"]["snapshot_every_days"])
    out = {c: pd.DataFrame(np.nan, index=dates, columns=ids) for c in GRAPH}
    graphs = []
    for k, s in enumerate(snaps):
        end = snaps[k + 1] if k + 1 < len(snaps) else dates.max() + pd.Timedelta(days=1)
        rows = (dates >= s) & (dates < end)
        if not rows.any():
            continue
        g = build_graph(inputs, s, cfg, km=km)
        graphs.append(g)
        Wd, Wc, Wf = g.matrix("distance", ids), g.matrix("price_corr", ids), g.matrix("flow_estimate", ids)
        sl = lambda f: f.loc[rows]  # noqa: E731
        out["gr_dist_chg_7"].loc[rows] = _wmean(sl(piv["px_chg_7"]), Wd).to_numpy()
        out["gr_dist_chg_14"].loc[rows] = _wmean(sl(piv["px_chg_14"]), Wd).to_numpy()
        out["gr_dist_spread"].loc[rows] = (sl(logp) - _wmean(sl(logp), Wd)).to_numpy()
        out["gr_dist_risen"].loc[rows] = _wmean(sl(risen), Wd).to_numpy()
        out["gr_corr_chg_7"].loc[rows] = _wmean(sl(piv["px_chg_7"]), Wc).to_numpy()
        out["gr_flow_up_chg_7"].loc[rows] = _wmean(sl(piv["px_chg_7"]), Wf).to_numpy()
        out["gr_n_corr"].loc[rows] = np.tile((Wc > 0).sum(axis=1).astype(float), (int(rows.sum()), 1))
    frame = pd.DataFrame({"date": np.repeat(dates.to_numpy(), len(ids)), "mandi_id": np.tile(ids, len(dates))})
    for c in GRAPH:
        frame[c] = out[c].to_numpy().ravel()  # row-major: date-major, mandi-minor, same order as above
    return frame, graphs
