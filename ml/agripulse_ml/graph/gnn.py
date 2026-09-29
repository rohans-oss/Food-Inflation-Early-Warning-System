"""Graph neural network forecaster (V2-3), plain torch, for the shared harness.

Architecture (small on purpose: 18 mandis, CPU):
  per mandi, the last `seq_len` days of its feature rows -> linear -> GRU -> h_i
  2 residual graph-convolution layers: h <- h + relu(A_hat @ (h W)),  A_hat = row-normalised (I + A)
  heads: 4 horizons x (p50, down-gap, up-gap) -> ordered p10 <= p50 <= p90 of the log price ratio,
         and a trained spike logit (14-day > +30%), BCE loss. Unlike TFT's, this spike probability is learned.

`use_graph=False` is the control: the same network with A = 0 (so A_hat = I, no messages between mandis).
GNN vs that control isolates what message passing adds; both are also compared with naive, seasonal naive and
LightGBM on the same folds.

Adjacency: the graph snapshot built as of the fold cutoff (data published before it), combining distance weights
and price-correlation weights (symmetric) and flow-ESTIMATE weights (produce source -> destination).

Time rules: a test row at t reads feature rows dated <= t only (the window ends at t; checked by
tests/test_graph.py tamper test). Standardisation statistics come from the fitting rows.
"""
import time

import numpy as np
import pandas as pd

from ..models import _cqr_offsets
from ..tft.model import HORIZONS

EXCLUDE = ("st_district", "st_state", "mandi_id")


class GraphFitCache:
    def __init__(self):
        self.fits: dict = {}


def _net(n_in: int, hidden: int, n_h: int):
    import torch
    from torch import nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.inp = nn.Linear(n_in, hidden)
            self.gru = nn.GRU(hidden, hidden, batch_first=True)
            self.g1, self.g2 = nn.Linear(hidden, hidden), nn.Linear(hidden, hidden)
            self.q = nn.Linear(hidden, n_h * 3)
            self.spike = nn.Linear(hidden, 1)

        def forward(self, x, A):  # x [B, L, N, F], A [N, N] row-normalised
            B, L, N, F = x.shape
            z = torch.relu(self.inp(x.permute(0, 2, 1, 3).reshape(B * N, L, F)))
            _, h = self.gru(z)
            h = h[-1].reshape(B, N, -1)
            h = h + torch.relu(A @ self.g1(h))
            h = h + torch.relu(A @ self.g2(h))
            o = self.q(h).reshape(B, N, n_h, 3)
            p50 = o[..., 0]
            p10 = p50 - nn.functional.softplus(o[..., 1])
            p90 = p50 + nn.functional.softplus(o[..., 2])
            return torch.stack([p10, p50, p90], dim=-1), self.spike(h).squeeze(-1)

    return Net()


class GraphForecaster:
    uses_history = True

    def __init__(self, cfg: dict, table, use_graph: bool, calibrate: bool = True, cache: GraphFitCache | None = None):
        self.cfg, self.use_graph, self.calibrate = cfg, use_graph, calibrate
        self.cache = cache or GraphFitCache()
        self.graphs = table.graphs
        self.features = [c for c in table.feature_columns if not c.startswith("gr_") and c not in EXCLUDE]
        self.ids = sorted(int(m) for m in table.df["mandi_id"].unique())
        self.name = "gnn" if use_graph else "gru_nograph"

    # ---------------------------------------------------------------- tensors

    def _grid(self, frame: pd.DataFrame, dates: pd.DatetimeIndex, cols: list[str]) -> np.ndarray:
        """[D, N, len(cols)] from long rows; missing (date, mandi) -> NaN."""
        pos_d = pd.Series(np.arange(len(dates)), index=dates)
        pos_n = {m: i for i, m in enumerate(self.ids)}
        out = np.full((len(dates), len(self.ids), len(cols)), np.nan, dtype=np.float32)
        f = frame[frame["date"].isin(dates) & frame["mandi_id"].isin(pos_n)]
        di = pos_d.loc[f["date"]].to_numpy()
        ni = f["mandi_id"].map(pos_n).to_numpy()
        out[di, ni] = f[cols].to_numpy(dtype=np.float32)
        return out

    def _inputs(self, frame: pd.DataFrame, dates: pd.DatetimeIndex, stats) -> np.ndarray:
        raw = self._grid(frame, dates, self.features)
        mu, sd = stats
        x = np.nan_to_num((raw - mu) / sd, nan=0.0)
        present = self._grid(frame, dates, ["price"])[..., :1]
        return np.concatenate([x, (~np.isnan(present)).astype(np.float32)], axis=-1)

    def _adjacency(self, cutoff: pd.Timestamp) -> np.ndarray:
        n = len(self.ids)
        A = np.zeros((n, n))
        if self.use_graph:
            g = [g for g in self.graphs if g.as_of <= cutoff]
            if g:
                g = g[-1]
                A = g.matrix("distance", self.ids) + g.matrix("price_corr", self.ids) + g.matrix("flow_estimate", self.ids)
        A = A + np.eye(n)
        return (A / A.sum(axis=1, keepdims=True)).astype(np.float32)

    # ---------------------------------------------------------------- fit

    def fit(self, train: pd.DataFrame, history: pd.DataFrame | None = None) -> "GraphForecaster":
        if history is None:
            raise ValueError("GraphForecaster needs history= (rows issued before the fold cutoff)")
        cutoff = history["date"].max() + pd.Timedelta(days=1)
        key = (cutoff, len(history), self.use_graph)
        if key not in self.cache.fits:
            self.cache.fits[key] = self._fit(train, history, cutoff)
        self.state = self.cache.fits[key]
        return self

    def _windows(self, X, idx):
        L = int(self.cfg["seq_len"])
        return np.stack([X[i - L + 1: i + 1] for i in idx])

    def _fit(self, train: pd.DataFrame, history: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
        import torch

        t0 = time.time()
        cfg = self.cfg
        torch.manual_seed(int(cfg["seed"]))
        np.random.seed(int(cfg["seed"]))
        L = int(cfg["seq_len"])
        cal_start = cutoff - pd.Timedelta(days=int(cfg["calibration_days"]))
        val_start = cal_start - pd.Timedelta(days=int(cfg["validation_days"]))
        fit_rows = train[train["date"] < val_start]
        vals = fit_rows[self.features].astype(float)
        stats = (vals.mean().to_numpy(np.float32), vals.std().replace(0, 1).fillna(1).to_numpy(np.float32))
        dates = pd.date_range(history["date"].min(), history["date"].max(), freq="D")
        X = self._inputs(history, dates, stats)
        Y = self._grid(train, dates, [f"target_h{h}" for h in HORIZONS])
        S = self._grid(train, dates, ["spike"])[..., 0]
        A = torch.tensor(self._adjacency(cutoff))
        dpos = {d: i for i, d in enumerate(dates)}
        labelled = np.where(~np.isnan(Y).all(axis=(1, 2)))[0]
        labelled = labelled[labelled >= L - 1]
        is_fit = dates[labelled] < val_start
        is_val = (dates[labelled] >= val_start) & (dates[labelled] < cal_start)
        tr_idx, va_idx = labelled[is_fit], labelled[is_val]

        net = _net(X.shape[-1], int(cfg["hidden"]), len(HORIZONS))
        opt = torch.optim.Adam(net.parameters(), lr=float(cfg["learning_rate"]), weight_decay=float(cfg["weight_decay"]))
        qs = torch.tensor([0.1, 0.5, 0.9])
        w_spike = float(cfg["spike_loss_weight"])

        def loss_on(idx):
            xb = torch.tensor(self._windows(X, idx))
            yb, sb = torch.tensor(Y[idx]), torch.tensor(S[idx])
            q, logit = net(xb, A)
            m = ~torch.isnan(yb)
            err = torch.nan_to_num(yb).unsqueeze(-1) - q  # [B, N, H, 3]
            pin = torch.maximum(qs * err, (qs - 1) * err).mean(-1)
            lq = (pin * m).sum() / m.sum().clamp(min=1)
            ms = ~torch.isnan(sb)
            ls = torch.nn.functional.binary_cross_entropy_with_logits(logit[ms], sb[ms]) if ms.any() else 0.0
            return lq + w_spike * ls

        best, best_state, bad, epochs = np.inf, None, 0, 0
        bs = int(cfg["batch_size"])
        for ep in range(int(cfg["max_epochs"])):
            net.train()
            perm = np.random.permutation(tr_idx)
            for k in range(0, len(perm), bs):
                opt.zero_grad()
                loss = loss_on(perm[k: k + bs])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()
            net.eval()
            with torch.no_grad():
                v = float(np.mean([loss_on(va_idx[k: k + 256]).item() for k in range(0, len(va_idx), 256)])) \
                    if len(va_idx) else float(loss.item())
            epochs = ep + 1
            if v < best - 1e-5:
                best, bad = v, 0
                best_state = {k: t.detach().clone() for k, t in net.state_dict().items()}
            else:
                bad += 1
                if bad >= int(cfg["early_stopping_patience"]):
                    break
        if best_state is not None:
            net.load_state_dict(best_state)
        state = {"net": net, "stats": stats, "A": A, "cutoff": cutoff, "epochs": epochs, "best_val": best,
                 "offsets": {}, "n_edges": int((A.numpy() > 0).sum() - len(self.ids))}
        cal = train[train["date"] >= cal_start]
        if len(cal) >= 50:
            q, _ = self._predict_rows(state, cal, history)
            for j, h in enumerate(HORIZONS):
                y = cal[f"target_h{h}"].to_numpy()
                ok = ~np.isnan(y)
                state["offsets"][h] = _cqr_offsets(y[ok], q[ok, j, 0], q[ok, j, 2]) if ok.sum() >= 50 else (0.0, 0.0)
        state["fit_seconds"] = round(time.time() - t0, 1)
        return state

    # ---------------------------------------------------------------- predict

    def _predict_rows(self, state: dict, rows: pd.DataFrame, context: pd.DataFrame):
        """[n, H, 3] log-ratio quantiles and [n] spike probability; row at t sees context rows dated <= t only."""
        import torch

        L = int(self.cfg["seq_len"])
        last = rows["date"].max()
        ctx = context[context["date"] <= last]
        dates = pd.date_range(min(ctx["date"].min(), rows["date"].min() - pd.Timedelta(days=L - 1)), last, freq="D")
        X = self._inputs(ctx, dates, state["stats"])
        dpos = pd.Series(np.arange(len(dates)), index=dates)
        npos = {m: i for i, m in enumerate(self.ids)}
        uniq = np.array(sorted(set(dpos.loc[rows["date"]].to_numpy())))
        q_all, s_all = {}, {}
        net = state["net"]
        net.eval()
        with torch.no_grad():
            for k in range(0, len(uniq), 128):
                idx = uniq[k: k + 128]
                q, logit = net(torch.tensor(self._windows(X, idx)), state["A"])
                for j, i in enumerate(idx):
                    q_all[i], s_all[i] = q[j].numpy(), torch.sigmoid(logit[j]).numpy()
        di = dpos.loc[rows["date"]].to_numpy()
        ni = rows["mandi_id"].map(npos).to_numpy()
        q = np.stack([q_all[d][n] for d, n in zip(di, ni)])
        s = np.array([s_all[d][n] for d, n in zip(di, ni)])
        return q, s

    def predict(self, test: pd.DataFrame, context: pd.DataFrame | None = None) -> dict:
        if context is None:
            raise ValueError("GraphForecaster needs context= (rows issued before the test window end)")
        q, s = self._predict_rows(self.state, test, context)
        out = {}
        for j, h in enumerate(HORIZONS):
            qq = q[:, j, :].copy()
            if self.calibrate:
                lo, hi = self.state["offsets"].get(h, (0.0, 0.0))
                qq[:, 0] = np.minimum(qq[:, 0] - lo, qq[:, 1])
                qq[:, 2] = np.maximum(qq[:, 2] + hi, qq[:, 1])
            for i, name in enumerate(("p10", "p50", "p90")):
                out[(h, name)] = qq[:, i]
        out["spike_prob"] = s
        return out
