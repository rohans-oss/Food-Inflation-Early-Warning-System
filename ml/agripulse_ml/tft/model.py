"""Temporal Fusion Transformer adapter for the shared harness (V2-2).

Library: pytorch-forecasting (TemporalFusionTransformer + TimeSeriesDataSet): the project doc's choice, and it
natively supports static covariates, known-future inputs, past-only inputs and multi-quantile loss.

Time axis = ISSUE DATE t from the V2 feature table. The target series is log b(t), the last PUBLISHED price,
which is known on t, so every row issued before the fold cutoff is legal training data. The decoder predicts
log b(t+1) ... log b(t+29); since b(t+7h+1) = p(t+7h), horizon h (weeks) is decoder step 7h (0-based index).

Inputs (decision (c), approved):
  static        mandi (categorical), st_lat, st_lon
  known-future  CALENDAR ONLY (deterministic). Weather forecasts are NOT fed as future values: most TFT
                examples feed actual future weather, which is a perfect-forecast leak.
  past-only     price + arrivals + observed weather + the weather forecast AS ISSUED on each day
                (wf_* columns: what the forecast said on t about the next 7/14 days), and log b itself.

Two outputs per fit (decision (d)): raw quantiles, and quantiles widened/narrowed by the same split-conformal
step V1 LightGBM uses (last `calibration_days` of the history are held out of fitting for this).
Spike probability (decision (e)) is an APPROXIMATION derived from the quantiles, see spike_probability().
"""
import logging
import warnings

import numpy as np
import pandas as pd

from ..features.store import _calendar
from ..models import _cqr_offsets

HORIZONS = (1, 2, 3, 4)
PRED_LEN = 29  # decoder steps b(t+1)..b(t+29): covers p(t+28) = b(t+29)
CALENDAR = ["cal_doy_sin", "cal_doy_cos", "cal_month", "cal_festival", "cal_festival_next14", "cal_season"]
STATIC_REALS = ["st_lat", "st_lon"]
QUANTILES = [0.1, 0.5, 0.9]
SPIKE_STEPS = range(1, 15)  # b(t+2)..b(t+15) = p(t+1)..p(t+14): the 14-day spike window

log = logging.getLogger("agripulse.tft")


def _quiet():
    for name in ("lightning", "lightning.pytorch", "pytorch_lightning", "lightning.fabric"):
        logging.getLogger(name).setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", module="lightning")
    warnings.filterwarnings("ignore", module="pytorch_forecasting")
    warnings.filterwarnings("ignore", category=UserWarning)


def spike_probability(q_log_steps: np.ndarray, log_base: np.ndarray, threshold_pct: float = 30.0) -> np.ndarray:
    """APPROXIMATION. P(price exceeds base*(1+thr) on any day in the next 14) is bounded below by the largest
    single-day exceedance probability; each day's CDF is interpolated linearly through (p10, p50, p90) in log
    space, extended with the outer segments' slopes, clipped to [0, 1].
    q_log_steps: [n, steps, 3] predicted log-price quantiles for the spike window."""
    thr = log_base[:, None] + np.log1p(threshold_pct / 100)
    q10, q50, q90 = q_log_steps[..., 0], q_log_steps[..., 1], q_log_steps[..., 2]
    eps = 1e-9
    lo = 0.1 + (thr - q10) * 0.4 / np.maximum(q50 - q10, eps)  # CDF on [q10, q50] segment, extended
    hi = 0.5 + (thr - q50) * 0.4 / np.maximum(q90 - q50, eps)  # CDF on [q50, q90] segment, extended
    cdf = np.where(thr <= q50, lo, hi)
    return np.clip(1 - cdf, 0, 1).max(axis=1)


class TFTFitCache:
    """One TFT fit per fold serves both the raw and the calibrated variant (they share the fit)."""

    def __init__(self):
        self.fits: dict = {}


class TFTForecaster:
    uses_history = True

    def __init__(self, cfg: dict, feature_table_columns: dict, calibrate: bool, cache: TFTFitCache | None = None,
                 spike_threshold_pct: float = 30.0, label_lag_days: int = 1):
        self.cfg = cfg
        self.calibrate = calibrate
        self.cache = cache or TFTFitCache()
        self.spike_threshold_pct = spike_threshold_pct
        self.label_lag = label_lag_days
        past = [c for c in feature_table_columns["past_only"]]
        issue_time_weather = [c for c in feature_table_columns["known_future"] if c.startswith("wf_")]
        self.unknown_reals = ["log_b"] + past + issue_time_weather
        self.known_reals = [c for c in CALENDAR if c in feature_table_columns["known_future"]]
        self.name = "tft_cqr" if calibrate else "tft_raw"
        self.info: dict = {}

    # ---------------------------------------------------------------- data

    def _frame(self, df: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
        f = df[["mandi_id", "date", "price", *[c for c in self.unknown_reals + self.known_reals + STATIC_REALS if c in df and c != "log_b"]]].copy()
        f["log_b"] = np.log(f["price"])
        f["time_idx"] = (f["date"] - origin).dt.days.astype(int)
        f["mandi"] = f["mandi_id"].astype(str)
        f["series"] = f["mandi"]
        num = self.unknown_reals + self.known_reals + STATIC_REALS
        f[num] = f[num].astype(float).fillna(0.0)  # TFT needs dense inputs; LightGBM keeps NaN natively
        return f

    def _dataset(self, frame: pd.DataFrame, **kw):
        from pytorch_forecasting import TimeSeriesDataSet
        from pytorch_forecasting.data import EncoderNormalizer, NaNLabelEncoder

        return TimeSeriesDataSet(
            frame, time_idx="time_idx", target="log_b", group_ids=["series"],
            max_encoder_length=int(self.cfg["max_encoder_length"]), min_encoder_length=int(self.cfg["max_encoder_length"]) // 3,
            max_prediction_length=PRED_LEN, min_prediction_length=PRED_LEN,
            static_categoricals=["mandi"], static_reals=STATIC_REALS,
            time_varying_known_reals=self.known_reals, time_varying_unknown_reals=self.unknown_reals,
            target_normalizer=EncoderNormalizer(), allow_missing_timesteps=True, add_relative_time_idx=True,
            categorical_encoders={"series": NaNLabelEncoder(add_nan=True), "mandi": NaNLabelEncoder(add_nan=True)},
            **kw,
        )

    # ---------------------------------------------------------------- fit

    def fit(self, train: pd.DataFrame, history: pd.DataFrame | None = None) -> "TFTForecaster":
        if history is None:
            raise ValueError("TFTForecaster needs history= (rows issued before the fold cutoff)")
        cutoff = history["date"].max() + pd.Timedelta(days=1)
        key = (cutoff, len(history))
        if key not in self.cache.fits:
            self.cache.fits[key] = self._fit(history, train, cutoff)
        self.state = self.cache.fits[key]
        return self

    def _fit(self, history: pd.DataFrame, train: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
        import time

        import lightning.pytorch as pl
        import torch
        from lightning.pytorch.callbacks import EarlyStopping
        from pytorch_forecasting import TemporalFusionTransformer
        from pytorch_forecasting.metrics import QuantileLoss

        _quiet()
        t0 = time.time()
        cfg = self.cfg
        pl.seed_everything(int(cfg["seed"]), workers=True, verbose=False)
        torch.set_num_threads(max(1, torch.get_num_threads()))
        origin = history["date"].min()
        cal_start = cutoff - pd.Timedelta(days=int(cfg["calibration_days"]))
        fit_end = cal_start  # fitting data: issue dates < cal_start (so decoders end before it)
        val_start = fit_end - pd.Timedelta(days=int(cfg["validation_days"]))
        full = self._frame(history, origin)
        fit_frame = full[full["date"] < fit_end]
        train_ds = self._dataset(fit_frame[fit_frame["date"] < val_start])
        val_ds = train_ds.from_dataset(train_ds, fit_frame, min_prediction_idx=int((val_start - origin).days),
                                       stop_randomization=True)
        tl = train_ds.to_dataloader(train=True, batch_size=int(cfg["batch_size"]), num_workers=0)
        vl = val_ds.to_dataloader(train=False, batch_size=int(cfg["batch_size"]) * 2, num_workers=0)
        model = TemporalFusionTransformer.from_dataset(
            train_ds, hidden_size=int(cfg["hidden_size"]), hidden_continuous_size=int(cfg["hidden_continuous_size"]),
            attention_head_size=int(cfg["attention_head_size"]), dropout=float(cfg["dropout"]),
            learning_rate=float(cfg["learning_rate"]), loss=QuantileLoss(quantiles=QUANTILES),
            log_interval=-1, reduce_on_plateau_patience=3,
        )
        trainer = pl.Trainer(
            max_epochs=int(cfg["max_epochs"]), accelerator="cpu", devices=1, gradient_clip_val=0.1,
            limit_train_batches=int(cfg["limit_train_batches"]), enable_progress_bar=False, enable_model_summary=False,
            logger=False, enable_checkpointing=False, deterministic=False,
            callbacks=[EarlyStopping(monitor="val_loss", patience=int(cfg["early_stopping_patience"]), mode="min")],
        )
        trainer.fit(model, train_dataloaders=tl, val_dataloaders=vl)
        state = {"model": model, "train_ds": train_ds, "origin": origin, "cutoff": cutoff,
                 "epochs": int(trainer.current_epoch), "fit_seconds": None, "offsets": {}}
        # conformal offsets on the held-out calibration window: issue dates in [cal_start, cutoff) whose
        # 4-week labels (+ publication lag) are known before the cutoff
        cal_rows = train[(train["date"] >= cal_start)]
        if len(cal_rows) >= 50:
            q = self._predict_steps(state, cal_rows, history)
            for h in HORIZONS:
                y = cal_rows[f"target_h{h}"].to_numpy()
                lb = np.log(cal_rows["price"].to_numpy())
                lo, hi = q[:, 7 * h, 0] - lb, q[:, 7 * h, 2] - lb
                ok = ~np.isnan(y)
                state["offsets"][h] = _cqr_offsets(y[ok], lo[ok], hi[ok]) if ok.sum() >= 50 else (0.0, 0.0)
        state["fit_seconds"] = round(time.time() - t0, 1)
        state["n_calibration_rows"] = int(len(cal_rows))
        log.info("TFT fold cutoff %s: %d epochs, %.0fs", cutoff.date(), state["epochs"], state["fit_seconds"])
        return state

    # ---------------------------------------------------------------- predict

    def _predict_steps(self, state: dict, rows: pd.DataFrame, context: pd.DataFrame) -> np.ndarray:
        """[n, PRED_LEN, 3] log-price quantiles for each row, using ONLY context rows of the same mandi with
        date <= that row's issue date, plus future calendar rows (known in advance)."""
        enc_len = int(self.cfg["max_encoder_length"])
        origin = state["origin"]
        ctx = self._frame(context, origin)
        frames = []
        for i, (mid, t) in enumerate(rows[["mandi_id", "date"]].itertuples(index=False)):
            past = ctx[(ctx["mandi_id"] == mid) & (ctx["date"] <= t)].tail(enc_len)  # <= t: never beyond the issue date
            fut_idx = pd.date_range(t + pd.Timedelta(days=1), periods=PRED_LEN, freq="D")
            fut = pd.DataFrame({"date": fut_idx})
            cal = _calendar(fut_idx)
            for c in self.known_reals:
                fut[c] = cal[c].to_numpy().astype(float)
            last = past.iloc[-1]
            for c in self.unknown_reals + STATIC_REALS + ["price"]:
                fut[c] = last[c]  # placeholders: unknown reals are only read in the encoder; decoder target unused
            fut["mandi_id"], fut["mandi"] = mid, str(mid)
            fut["time_idx"] = (fut["date"] - origin).dt.days.astype(int)
            s = pd.concat([past, fut], ignore_index=True)
            s["series"] = f"s{i}"
            frames.append(s)
        pred_frame = pd.concat(frames, ignore_index=True)
        ds = state["train_ds"].from_dataset(state["train_ds"], pred_frame, predict=True, stop_randomization=True)
        dl = ds.to_dataloader(train=False, batch_size=256, num_workers=0)
        out = state["model"].predict(dl, mode="quantiles", return_index=True,
                                     trainer_kwargs={"logger": False, "enable_progress_bar": False, "accelerator": "cpu"})
        q = out.output.detach().cpu().numpy()  # [n, PRED_LEN, 3] in log-price
        order = out.index["series"].str[1:].astype(int).to_numpy()
        res = np.empty_like(q)
        res[order] = q
        return np.sort(res, axis=2)

    def predict(self, test: pd.DataFrame, context: pd.DataFrame | None = None) -> dict:
        if context is None:
            raise ValueError("TFTForecaster needs context= (rows issued before the test window end)")
        q = self._predict_steps(self.state, test, context)
        lb = np.log(test["price"].to_numpy())
        out = {}
        for h in HORIZONS:
            qq = q[:, 7 * h, :] - lb[:, None]
            if self.calibrate:
                lo_adj, hi_adj = self.state["offsets"].get(h, (0.0, 0.0))
                qq[:, 0] = np.minimum(qq[:, 0] - lo_adj, qq[:, 1])
                qq[:, 2] = np.maximum(qq[:, 2] + hi_adj, qq[:, 1])
            for i, qn in enumerate(("p10", "p50", "p90")):
                out[(h, qn)] = qq[:, i]
        out["spike_prob"] = spike_probability(q[:, list(SPIKE_STEPS), :], lb, self.spike_threshold_pct)
        return out
