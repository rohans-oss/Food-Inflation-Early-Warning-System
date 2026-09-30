# V2-5 results: ablation, what each feature group adds

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT** for every price number here. Mixed rows are labelled:
> `satellite` uses REAL NDVI against SYNTHETIC prices, and `transit` uses SIMULATED trips. The **real-so-far** run
> is at the end: not enough real data yet, as expected.

## Bottom line

| group added to LightGBM (prices + weather base) | effect on pinball, 3 draws × 4 folds | how to read it |
|---|---|---|
| **graph** | +1.1% to +3.0%, better on 12 of 12 draw-horizons | consistent but small; matches V2-3. Not evidence that geography matters (docs/graph-results.md) |
| **satellite** | +0.3% to +0.7% pooled; +0.8% to +2.3% on the 5 mandis that have NDVI (better in 50 of 60 mandi-draw-horizons) | **not a crop signal.** NDVI acts as a seasonal clock (below). Plumbing check only |
| **transit** | +1.4% to +3.1%, 12 of 12 | **built in by construction:** simulated trips carry tomorrow's synthetic arrivals. It shows the pipeline carries the signal |
| **all groups** | +4.4% to +6.4%, 12 of 12 | the sum of the rows above, including the built-in transit gain |
| **weather** (removed instead of added) | removing it **helps**: prices-only is +1.6% to +6.4% better than prices + weather, but only 2 of 3 draws, range −4.8% to +16.7% | noisy; the weather features look like overfitting noise here. See backlog item 13 |

**Against naive**, nothing is reliably better. `lgbm_all` is +2.6% to +3.4% on average but wins only 2 of 3 draws at
most horizons. `lgbm_prices` is +5.1% at 4 weeks with the same 2 of 3. The V2-0 finding stands: on this generator no
model beats "today's price, with the spread of past changes as its range" reliably across draws.

**Reproducibility check:** `lgbm_base` reproduces V2-2's and V2-3's LightGBM numbers to the decimal (−2.5 / −3.1 /
−2.7 / −1.7% vs naive). All three phases share one harness and one set of folds.

## Why the satellite row moves at all

Real NDVI cannot cause synthetic prices, so the small gain needed an explanation before being reported. Day of year
explains 59% of `sat_ndvi_30`'s variance. Its correlation with the 4-week target is −0.33 raw but −0.10 once day of
year is removed from both. The anomaly feature (`sat_ndvi_anom`, season removed) correlates −0.03. The synthetic
price cycle has a second annual harmonic, while the calendar features carry only the first (one sine / cosine pair),
so NDVI's seasonal shape fills that gap. This is a **seasonality effect, not a crop signal**. It also points to a
cheap fix, a second calendar harmonic (backlog item 14), that was not applied here (rule 11).

## Numbers

Pinball loss, pooled over 18 mandis, mean of draws 7 / 1 / 2 (Rs/quintal, lower is better):

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| seasonal_naive | 187.5 | 269.2 | 338.7 | 389.8 |
| naive | 131.1 | 189.5 | 237.2 | 275.0 |
| lgbm_prices | 131.5 | 184.0 | 228.4 | 260.6 |
| lgbm_base (prices + weather) | 133.2 | 193.8 | 244.2 | 286.3 |
| lgbm_graph | 129.3 | 189.8 | 240.7 | 284.2 |
| lgbm_satellite | 132.7 | 192.9 | 242.7 | 286.2 |
| lgbm_transit | 131.4 | 187.3 | 237.3 | 281.1 |
| lgbm_all | 126.8 | 181.2 | 230.6 | 274.3 |

% better than **lgbm_base**: mean (draws better, of 3) [worst, best draw]

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| naive | +2.2 (1/3) [-2.5, +9.2] | +2.8 (2/3) [-3.4, +9.2] | +2.6 (2/3) [-1.2, +5.1] | +1.3 (2/3) [-5.7, +8.8] |
| lgbm_prices | +1.6 (2/3) [-1.4, +5.6] | +4.5 (2/3) [-2.2, +10.0] | +4.8 (2/3) [-4.8, +14.8] | +6.4 (2/3) [-1.5, +16.7] |
| lgbm_graph | +3.0 (3/3) [+1.7, +4.1] | +2.2 (3/3) [+1.6, +3.5] | +1.6 (3/3) [+0.8, +2.0] | +1.1 (3/3) [+0.2, +2.1] |
| lgbm_satellite | +0.4 (2/3) [-0.2, +1.3] | +0.4 (2/3) [-0.6, +1.1] | +0.7 (3/3) [+0.4, +1.2] | +0.3 (2/3) [-0.8, +1.6] |
| lgbm_transit | +1.4 (3/3) [+0.9, +2.1] | +3.1 (3/3) [+0.9, +5.1] | +2.5 (3/3) [+1.0, +4.2] | +2.1 (3/3) [+1.4, +2.7] |
| lgbm_all | +4.9 (3/3) [+3.5, +6.1] | +6.4 (3/3) [+5.2, +7.6] | +5.2 (3/3) [+3.7, +7.1] | +4.4 (3/3) [+3.9, +5.3] |

% better than **naive**:

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| lgbm_prices | -0.7 (2/3) [-4.0, +1.0] | +1.7 (2/3) [-3.7, +7.6] | +2.5 (2/3) [-3.6, +10.2] | +5.1 (2/3) [-2.5, +9.3] |
| lgbm_base | -2.5 (2/3) [-10.2, +2.4] | -3.1 (1/3) [-10.1, +3.3] | -2.7 (1/3) [-5.4, +1.2] | -1.7 (1/3) [-9.6, +5.4] |
| lgbm_graph | +0.6 (2/3) [-5.7, +5.7] | -0.8 (1/3) [-6.3, +4.8] | -1.1 (1/3) [-4.6, +3.2] | -0.7 (1/3) [-9.4, +7.3] |
| lgbm_satellite | -2.1 (2/3) [-10.2, +3.7] | -2.7 (1/3) [-9.5, +2.7] | -2.0 (1/3) [-5.0, +1.7] | -1.5 (2/3) [-10.5, +5.4] |
| lgbm_transit | -1.0 (2/3) [-7.9, +3.2] | +0.1 (2/3) [-6.3, +4.1] | -0.1 (1/3) [-1.3, +2.1] | +0.3 (2/3) [-8.1, +7.9] |
| lgbm_all | +2.6 (2/3) [-3.5, +7.5] | +3.4 (2/3) [-3.2, +8.3] | +2.7 (3/3) [+1.2, +4.9] | +2.7 (2/3) [-5.2, +10.4] |

p10–p90 coverage (target 80%). Every LightGBM variant is too narrow, as in V2-2 (backlog item 12):

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| naive | 84.1 | 83.7 | 83.6 | 82.3 |
| lgbm_prices | 75.1 | 71.8 | 66.7 | 67.4 |
| lgbm_base | 72.9 | 67.0 | 61.9 | 59.5 |
| lgbm_all | 76.1 | 70.4 | 64.0 | 61.3 |

Spike warning (> 30% rise within 14 days, alert at 0.5; base-rate Brier ≈ 0.182):

| model | recall | precision | false-alarm rate | Brier |
|---|---|---|---|---|
| lgbm_prices | 0.194 | 0.435 | 0.072 | 0.176 |
| lgbm_base | 0.099 | 0.330 | 0.069 | 0.183 |
| lgbm_graph | 0.125 | 0.372 | 0.068 | 0.179 |
| lgbm_satellite | 0.106 | 0.358 | 0.065 | 0.182 |
| lgbm_transit | 0.095 | 0.304 | 0.065 | 0.182 |
| lgbm_all | 0.129 | 0.467 | 0.048 | 0.174 |

## Real-so-far data

`python -m agripulse_ml.ablation --provenance real` runs the same variants on real rows only. Result in this build
workspace (demo database): **`not_enough_real_data`, no real price rows**. On the live system real Agmarknet prices
start on 2026-09-25, so it will also report not enough data until the readiness monitor turns green (about 13
months of history). The 2,610 real satellite rows are loaded, and there are 0 real trips. When real prices mature,
this command is the whole re-run: no code changes.

## Setup

| | |
|---|---|
| Table | `prices+weather+satellite+graph+transit`, synthetic seeds 7 / 1 / 2, 18 mandis, 2022-01-01 → 2026-09-25 |
| Folds | shared harness, 4 × 28-day walk-forward folds, identical for every variant |
| Model | V1 LightGBM quantile model and hyper-parameters; only the input columns change (`ablation.columns_for`) |
| Satellite | `data/satellite/observations.csv` (real, 2 districts: 5 of 18 mandis have NDVI) |
| Transit | trip batches simulated from synthetic arrivals, leaving 4–30 h before arrival, planned duration ± 20–25% |
| Run time | 7.6 minutes for 3 draws on 2 CPU cores |
| Raw results | `docs/results/ablation-synthetic.csv`, `ablation-synthetic-meta.json`, `ablation-real.json` |
