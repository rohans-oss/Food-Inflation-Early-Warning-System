# V2-2 results: Temporal Fusion Transformer vs baselines

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Every number on this page comes from the synthetic price
> generator (`data_provenance = synthetic`). It shows that the pipeline, the comparison and the leakage controls work.
> It says nothing about how TFT will do on real Agmarknet prices. Real history becomes usable around late 2027
> (Admin → Real-data readiness).

## Bottom line

**TFT loses to the plain naive forecast (today's price, with the spread of past price changes as its range) on this data, clearly and consistently.**
Averaged over 3 synthetic draws, its pinball loss is 15–33% worse than naive, depending on horizon and variant. It
beats naive in only 1 of 3 draws, and only at 3–4 weeks. Its intervals are also too narrow: the p10–p90 band holds
the actual price 46–59% of the time before calibration, against a target of 80%. Calibration (CQR) improves this in
only one draw.

The V1 LightGBM model, retrained on the same V2 table, also fails to beat naive on average (−1.7% to −3.1%). This
repeats the V2-0 finding in docs/backtest-synthetic.md.

As approved, nothing was tuned after seeing these numbers. The configuration in `config/models.toml` was fixed
before the run and is the one reported here. The first draw alone (seed 7) looked like a 3–4 week win (+4% / +12%).
The other two draws reversed that, which is why no conclusion rests on one draw.

## Setup

| | |
|---|---|
| Data | Pinned synthetic generator, 18 mandis, 2022-01-01 → 2026-09-25, draws with seed 7, 1, 2 |
| Feature table | V2-1 `prices+weather` (+ calendar), publication lags applied, label lag 1 day |
| Folds | Shared harness, 4 walk-forward folds of 28 days, cutoffs 2026-05-09, 06-06, 07-04, 08-01. **Same folds, rows and metrics for every model** |
| Models | `seasonal_naive`, `naive`, `lightgbm_v1` (the V1 LightGBM model and hyper-parameters on the V2 table), `tft_raw`, `tft_cqr` |
| TFT | pytorch-forecasting 1.8, hidden 16, 2 attention heads, encoder 90 days, decoder 29 days, quantile loss (0.1/0.5/0.9), up to 8 epochs × 120 batches, early stopping on the last 60 days before the calibration window |
| TFT inputs | Static: mandi, lat/lon. Known future: **calendar only**. Past only: prices, arrivals, observed weather, and the weather forecast *as issued* on each day |
| Calibration | `tft_cqr`: split-conformal offsets from a 120-day window just before each cutoff, held out of fitting (the same step as V1 LightGBM) |
| Raw results | `docs/results/tft-synthetic.csv` (long table, per mandi and pooled), `docs/results/tft-synthetic-timings.json` |

## Accuracy: pinball loss, pooled over mandis (Rs/quintal, lower is better)

Mean over the 3 draws:

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| seasonal_naive | 187.5 | 269.2 | 338.7 | 389.8 |
| naive | **131.1** | **189.5** | **237.2** | **275.0** |
| lightgbm_v1 | 133.2 | 193.8 | 244.2 | 286.3 |
| tft_raw | 169.0 | 231.3 | 287.5 | 327.3 |
| tft_cqr | 176.2 | 240.1 | 294.0 | 337.2 |

**% better than naive, per draw** (positive = beats naive):

| model | draw | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| seasonal_naive | 7 | -78.6 | -124.5 | -177.5 | -212.1 |
| seasonal_naive | 1 | -16.2 | -8.9 | +0.2 | +7.4 |
| seasonal_naive | 2 | -44.7 | -26.1 | -15.4 | -9.2 |
| **seasonal_naive** | **mean (wins)** | **-46.5** (0/3) | **-53.2** (0/3) | **-64.3** (1/3) | **-71.3** (1/3) |
| lightgbm_v1 | 7 | -10.2 | -10.1 | -3.9 | +5.4 |
| lightgbm_v1 | 1 | +2.4 | +3.3 | +1.2 | -1.0 |
| lightgbm_v1 | 2 | +0.3 | -2.6 | -5.4 | -9.6 |
| **lightgbm_v1** | **mean (wins)** | **-2.5** (2/3) | **-3.1** (1/3) | **-2.7** (1/3) | **-1.7** (1/3) |
| tft_raw | 7 | -18.1 | -5.1 | +2.0 | +7.1 |
| tft_raw | 1 | -19.8 | -22.0 | -28.0 | -32.4 |
| tft_raw | 2 | -44.9 | -30.8 | -26.3 | -20.3 |
| **tft_raw** | **mean (wins)** | **-27.6** (0/3) | **-19.3** (0/3) | **-17.4** (1/3) | **-15.2** (1/3) |
| tft_cqr | 7 | -19.1 | -6.2 | +3.7 | +11.8 |
| tft_cqr | 1 | -25.0 | -34.3 | -45.0 | -58.1 |
| tft_cqr | 2 | -53.7 | -31.2 | -20.8 | -13.0 |
| **tft_cqr** | **mean (wins)** | **-32.6** (0/3) | **-23.9** (0/3) | **-20.7** (1/3) | **-19.8** (1/3) |

Per-mandi wins against naive (out of 18 mandis) tell the same story. `tft_cqr` beats naive in 2–5 mandis on draw 1,
0–3 on draw 2, and 5–15 on draw 7.

**Median error (MAPE of p50, %):** naive 15.4 / 20.7 / 25.1 / 28.4, lightgbm_v1 14.8 / 19.2 / 21.9 / 24.3,
TFT 18.3 / 23.3 / 27.5 / 30.2. The TFT median itself is worse than naive at every horizon, so the gap is not only
an interval-width problem. (LightGBM's median is slightly better than naive; its intervals are what lose.)

## Interval coverage: how often the p10–p90 band holds the actual price (target 80%)

Mean over draws:

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| naive | 84.1 | 83.7 | 83.6 | 82.3 |
| lightgbm_v1 | 72.9 | 67.0 | 61.9 | 59.5 |
| tft_raw | 58.7 | 51.8 | 47.3 | 46.2 |
| tft_cqr | 66.6 | 61.2 | 56.2 | 52.2 |

Per draw, raw → calibrated:

| draw | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| 7 | 54.8 → 70.1 | 52.7 → 70.1 | 53.0 → 75.3 | 56.7 → 80.2 |
| 1 | 69.3 → 71.2 | 59.7 → 61.0 | 52.4 → 51.1 | 49.0 → **40.9** |
| 2 | 52.1 → 58.4 | 42.9 → 52.4 | 36.5 → 42.2 | 32.8 → 35.6 |

Raw TFT quantiles are badly over-confident. Calibration reaches about 80% on draw 7 only. On draw 1 at 4 weeks it
makes coverage *worse*, because the offsets shift the band rather than widen it (see the investigation below).

## Spike warning (price rises more than 30% within 14 days)

| model | recall | precision | false-alarm rate | Brier |
|---|---|---|---|---|
| seasonal_naive | 0.000 | — | 0.000 | 0.182 |
| naive | 0.000 | — | 0.000 | 0.182 |
| lightgbm_v1 | 0.099 | 0.330 | 0.069 | 0.183 |
| tft_raw / tft_cqr | 0.216 | 0.367 | 0.121 | 0.214 |

Averages over 3 draws at alert probability 0.5, with about 473 spike days per draw. The TFT spike probability is an
**approximation**: it is derived from the quantiles (the largest single-day exceedance probability in the next 14
days, with the CDF interpolated through p10/p50/p90), not from a trained classifier. It catches about twice as many
spikes as LightGBM at similar precision, but with nearly twice the false alarms. Its Brier score (0.214) is worse
than always predicting the base rate (0.182), so its probabilities are not calibrated and should not be shown as
probabilities.

## Investigation: why TFT loses (no tuning, diagnosis only)

1. **Unstable calibration windows.** The CQR offsets, fitted on the 120 days before each cutoff, swing widely from
   fold to fold. For draw 1 at 4 weeks the upper offset goes 0.06 → 0.20 → 0.27 → 0.35 in log-price, and the lower
   offset turns negative (−0.03 → −0.20), which pulls the lower bound *up*. The synthetic series has seasonal regime changes, so the error pattern in the calibration
   window often doesn't match the next 28 days. Split-conformal assumes those two windows behave alike. The same
   issue explains why V1 LightGBM, with the same calibration step, also under-covers (59–73%).
2. **Early stopping fires almost immediately.** Every fit stopped after 3–6 epochs with patience 2, so the best
   validation epoch was the 1st to 4th (≈120–480 batches). Either the 60-day validation window is too noisy to guide
   training, or the model overfits the fitting window fast. Telling these apart needs a tuning study, which rule 11
   rules out for this phase. It is noted as an open question, not fixed.
3. **Naive is a strong baseline for this generator.** Synthetic prices are close to a random walk around a seasonal
   level, and naive's range is the empirical spread of past h-week changes over the whole training window, which holds about 83%. A model has to know when the
   level will shift to beat that, and neither TFT nor LightGBM does so reliably across draws.
4. **Draw-to-draw spread is larger than any model effect.** The same TFT configuration ranges from +12% to −58% at
   4 weeks across draws. Any single-draw claim, positive or negative, would be noise.

What this does **not** show: that TFT is a bad choice for real tomato prices. Real series have structure the
generator lacks (supply shocks visible in arrivals, weather-driven gluts). The question can be asked for real once
readiness turns green. At that point the same experiment runs by changing one line
(`[tft] data_provenance = "real"`).

## Compute

| draw | fold cutoff | epochs | fit seconds |
|---|---|---|---|
| 7 | 2026-05-09 | 6 | 1592* |
| 7 | 2026-06-06 | 4 | 368 |
| 7 | 2026-07-04 | 3 | 280 |
| 7 | 2026-08-01 | 3 | 288 |
| 1 | 2026-05-09 | 5 | 443 |
| 1 | 2026-06-06 | 4 | 373 |
| 1 | 2026-07-04 | 3 | 281 |
| 1 | 2026-08-01 | 3 | 287 |
| 2 | 2026-05-09 | 5 | 445 |
| 2 | 2026-06-06 | 3 | 294 |
| 2 | 2026-07-04 | 3 | 297 |
| 2 | 2026-08-01 | 5 | 447 |

\* This fit shared the 2 CPU cores with a test run; the uncontended cost is about 450 s.

Wall time per draw (all 5 models, 4 folds): 44.6 min (contended), 25.5 min, 27.2 min. That is about 97 minutes in
total on 2 CPU cores, with no GPU. One fit serves both TFT variants. LightGBM takes seconds per fold.

## Reproduce

```bash
pip install -e .[tft]
python -m agripulse_ml.tft.experiment --record   # ~80 min on 2 cores; writes docs/results/tft-synthetic.*
python -m agripulse_ml.tft.experiment --seeds 7 --folds 1   # quick look, ~5 min
```

TFT runs are not bit-for-bit deterministic on CPU (`deterministic=False` for speed), so reruns can differ by a
fraction of a percent. The conclusions above do not depend on that.

## Status of TFT in the product

TFT is **not** the display model. `config/models.toml` keeps `[display] model = "lightgbm_quantile"` and
`[tft] write_forecasts = false`. Nothing a user sees changes in V2-2.
