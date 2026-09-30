# Forecast range calibration (Pre-V3 B-1)

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Every number in this document comes from the 8 synthetic
> datasets of V2-0 (`data_provenance = "synthetic"`). Real Agmarknet history started on 2026-09-25, so the live
> calibration status for real forecasts is **not yet applicable** (see "Live status" below).

## Summary

- **Diagnosis.** V1 LightGBM, the model users see, is too narrow mainly at longer horizons. Its mean p10–p90
  coverage across the 8 datasets is 79.4 / 78.4 / 76.8 / 74.2% at 1–4 weeks, against a target of 80%. Misses are
  slightly skewed upwards: prices end above p90 more often than below p10. The p50 is close to unbiased
  (median error −0.1% to −2.4%), but its typical error grows from 14% to 24%.
- **Fix.** The calibration step is a post-processing wrapper that works on any quantile model. It widens (or
  narrows) each horizon's range using the model's own track record from the last 365 days of published outcomes.
- **Result on the mean.** Mean coverage after calibration is 80.8 / 79.7 / 79.1 / 78.6%, inside the ±5-point
  tolerance at every horizon. Before calibration, the 4-week horizon was outside it.
- **Result per dataset.** The spread between datasets is **not** fixed. After calibration, 3–5 of the 8 datasets
  are within ±5 points at each horizon, compared with 1–7 before. Two datasets that already over-covered (3 and 6)
  get wider still. One dataset (5) gets worse at 1 and 2 weeks.
- **Cost.** Wider ranges make the mean pinball loss 0.6–2.9% worse for LightGBM, and it improved on only
  6 of 32 dataset-horizons. Calibration buys honest average coverage at a small cost in sharpness. It does not
  make the model more accurate.
- **The model users see is unchanged** (rule 19). `[display] model` is still `lightgbm_quantile`. Calibration
  changes only the width of the displayed range and is labelled wherever the range is shown.

## Why the numbers differ from the brief's "59–73%"

The brief quotes 59–73%. Those figures come from the V2-5 ablation's feature-store LightGBM variants, which ran on
3 draws and whose p10–p90 coverage was 59.5–76.1%. The raw TFT was lower still, at 46–59%. Rule 18 says to
validate on V2-0's 8 datasets, so this study uses those datasets with the V1 LightGBM, which is the displayed
model. On those datasets it was less overconfident (74–79% mean). The wrapper is model-agnostic and applies to the
ablation variants, the TFT (`tft/forecast.py` already calls it) and a future GNN. It was **not** re-run on the
ablation or TFT configurations in this phase; that is listed under known issues.

## Method

Pre-registered in `config/calibration.toml` before the study ran and not changed afterwards:

- Target 80%, tolerance ±5 points.
- Primary method `track_record`, with `window_days = 365` and `min_scores = 100`.
- ACI `gamma = 0.005`.

**Track-record conformal (primary).** This is conformalized quantile regression, split-conformal style, applied
per horizon:

1. For each past forecast whose actual price is already published, compute two scores:
   `lo = q10 − y` and `hi = y − q90`, both in log price.
2. On issue date *t*, use only scores with `known_on < t`, where `known_on` is the target date plus the
   publication lag, and only from the last 365 days.
3. Widen p10 by the finite-sample (1 − 0.1)(n+1)/n quantile of `lo`, and p90 by the same quantile of `hi`.
   Each side gets its own offset because the misses are asymmetric.
4. If fewer than 100 scores exist, the range is left as it is and marked `not_yet_applicable`.
5. The result is clipped so that p10 ≤ p50 ≤ p90. The p50 is never moved.

**Adaptive Conformal Inference (ACI, reported alongside).** This follows Gibbs & Candès (2021). The miss level
adapts once per forecasting round, using that round's realised miss rate over the calibrated rows. The level is
clipped to [0.001, 0.5].

**Why this method.**
- It is model-agnostic: it needs only a model's p10/p50/p90 and the outcomes.
- It uses only information available at forecast time.
- It needs no retraining, so V1/V2 model logic is untouched (rule 17).
- A one-year track record matches what the live system will have once real data accumulates.

**Folds.** Building a track record needs a year of out-of-sample forecasts. The walk-forward was therefore
extended backwards by 13 folds of 28 days (364 days). Only the final 8 folds are scored. `calibration_study.py`
asserts that their cutoffs are identical to V2-0's `FoldSpec(n_folds=8)`. In seed 1 these are 2026-01-16 through
2026-07-31. The "before" coverage reproduces V2-0 exactly; seed 1 LightGBM gives 81.0 / 77.5 / 72.5 / 68.9.
Calibration was `applied` on 100% of scored rows for every dataset, model and horizon (`calibration-meta.json`).

**Disclosure: a bug found and fixed before the study numbers were read.** The first ACI version updated its level
after every single outcome. That meant 18 updates per day, arriving with a 28-day delay, and it oscillated: it
reached 71.7% coverage on a stationary controlled stream whose correct answer is 80%. I changed it to one update
per forecasting round, which is the formulation in the paper. It then reaches 79.9% on the same controlled stream
(test: `test_overconfident_ranges_are_calibrated_to_the_target`). The fix was found and verified on the controlled
test, not by looking at study results. Neither method's settings were changed after the study ran.

## 1. Diagnosis (before calibration, V1 LightGBM)

Mean over the 8 datasets:

| horizon | coverage | below p10 (target 10) | above p90 (target 10) | p50 MAPE | p50 median bias |
|---|---|---|---|---|---|
| 1 wk | 79.4 | 9.7 | 10.9 | 14.4% | -0.1% |
| 2 wk | 78.4 | 10.4 | 11.3 | 19.0% | -0.6% |
| 3 wk | 76.8 | 10.8 | 12.4 | 21.7% | -1.7% |
| 4 wk | 74.2 | 12.0 | 13.9 | 24.4% | -2.4% |

- Under-coverage grows with horizon; it is not uniform. At 1 week the range is on target. At 4 weeks it misses
  about 1 price in 4 instead of 1 in 5.
- Both tails miss more at longer horizons, and the upper tail misses more: prices rise above the range more often
  than they fall below it. This fits a series with sudden spikes.
- The p50 is nearly unbiased. The problem is range width, not a shifted centre.

Per dataset and horizon:

| dataset | horizon | coverage | below p10 | above p90 | p50 MAPE | p50 median bias |
|---|---|---|---|---|---|---|
| 1 | 1 wk | 81.0 | 9.3 | 9.6 | 13.7% | -0.7% |
|  | 2 wk | 77.5 | 12.5 | 10.0 | 16.9% | -1.5% |
|  | 3 wk | 72.5 | 13.4 | 14.0 | 19.1% | -2.1% |
|  | 4 wk | 68.9 | 16.2 | 14.9 | 20.7% | -1.3% |
| 2 | 1 wk | 76.2 | 11.7 | 12.1 | 14.4% | +2.9% |
|  | 2 wk | 70.7 | 13.5 | 15.7 | 21.7% | +6.1% |
|  | 3 wk | 66.5 | 15.4 | 18.1 | 29.8% | +10.5% |
|  | 4 wk | 62.1 | 17.9 | 19.9 | 35.8% | +14.2% |
| 3 | 1 wk | 82.1 | 12.4 | 5.5 | 15.4% | -3.9% |
|  | 2 wk | 84.9 | 12.7 | 2.4 | 19.5% | -8.3% |
|  | 3 wk | 85.8 | 13.3 | 0.9 | 21.6% | -12.8% |
|  | 4 wk | 82.7 | 17.2 | 0.1 | 22.9% | -16.7% |
| 4 | 1 wk | 81.9 | 7.2 | 10.9 | 12.3% | -0.8% |
|  | 2 wk | 83.3 | 7.0 | 9.8 | 14.4% | -2.7% |
|  | 3 wk | 79.8 | 8.1 | 12.1 | 16.4% | -5.5% |
|  | 4 wk | 74.5 | 7.7 | 17.8 | 22.7% | -7.3% |
| 5 | 1 wk | 78.9 | 5.9 | 15.1 | 16.1% | -2.9% |
|  | 2 wk | 76.9 | 4.3 | 18.8 | 23.9% | -3.7% |
|  | 3 wk | 75.6 | 3.9 | 20.6 | 28.7% | -6.8% |
|  | 4 wk | 73.0 | 3.1 | 23.9 | 33.0% | -9.9% |
| 6 | 1 wk | 83.8 | 6.3 | 10.0 | 13.3% | +0.9% |
|  | 2 wk | 86.7 | 5.7 | 7.6 | 16.3% | +1.2% |
|  | 3 wk | 88.1 | 5.1 | 6.8 | 17.0% | +1.3% |
|  | 4 wk | 90.3 | 3.2 | 6.4 | 17.7% | +1.0% |
| 7 | 1 wk | 73.0 | 14.3 | 12.7 | 14.8% | +4.2% |
|  | 2 wk | 72.0 | 15.5 | 12.5 | 19.2% | +5.0% |
|  | 3 wk | 71.2 | 15.9 | 12.9 | 18.9% | +2.7% |
|  | 4 wk | 68.5 | 18.1 | 13.3 | 19.6% | +2.6% |
| 8 | 1 wk | 78.1 | 10.6 | 11.3 | 15.0% | -0.4% |
|  | 2 wk | 74.8 | 11.9 | 13.2 | 20.1% | -0.6% |
|  | 3 wk | 75.0 | 11.5 | 13.5 | 22.3% | -1.0% |
|  | 4 wk | 73.1 | 12.2 | 14.7 | 22.6% | -2.0% |

## 2. Before / after per dataset: V1 LightGBM (the displayed model)

Coverage of the p10–p90 range in % (target 80). **Bold** means outside ±5 points.

| method | statistic | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| before | mean coverage | 79.4 | 78.4 | 76.8 | 74.2 |
|  | range (min–max) | 73.0–83.8 | 70.7–86.7 | 66.5–88.1 | 62.1–90.3 |
|  | mean abs gap from 80 | 2.8 | 5.4 | 6.7 | 9.1 |
|  | datasets within ±5 (of 8) | 7 | 4 | 3 | 1 |
| after (track record, primary) | mean coverage | 80.8 | 79.7 | 79.1 | 78.6 |
|  | range (min–max) | 73.8–87.3 | 69.8–90.4 | 71.2–92.3 | 72.0–92.7 |
|  | mean abs gap from 80 | 3.1 | 4.7 | 5.9 | 6.4 |
|  | datasets within ±5 (of 8) | 5 | 5 | 3 | 4 |
| after (ACI) | mean coverage | 80.3 | 80.0 | 78.9 | 78.2 |
|  | range (min–max) | 72.5–86.8 | 72.6–89.0 | 71.8–91.0 | 67.9–89.3 |
|  | mean abs gap from 80 | 3.1 | 3.6 | 5.7 | 6.1 |
|  | datasets within ±5 (of 8) | 6 | 5 | 4 | 3 |

| dataset (seed) | method | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| 1 | before | 81.0 | 77.5 | **72.5** | **68.9** |
|  | after (track record, primary) | 82.0 | 78.6 | **74.3** | **72.0** |
|  | after (ACI) | 83.3 | 80.1 | 76.9 | 76.0 |
| 2 | before | 76.2 | **70.7** | **66.5** | **62.1** |
|  | after (track record, primary) | 79.7 | 78.1 | 78.7 | 77.3 |
|  | after (ACI) | 82.0 | 79.5 | 77.7 | **74.6** |
| 3 | before | 82.1 | 84.9 | **85.8** | 82.7 |
|  | after (track record, primary) | **85.0** | **86.1** | **87.9** | **87.1** |
|  | after (ACI) | **86.8** | **89.0** | **91.0** | **89.3** |
| 4 | before | 81.9 | 83.3 | 79.8 | **74.5** |
|  | after (track record, primary) | 81.1 | 81.5 | 79.6 | 75.9 |
|  | after (ACI) | 79.5 | 78.9 | 77.0 | 76.2 |
| 5 | before | 78.9 | 76.9 | 75.6 | **73.0** |
|  | after (track record, primary) | **73.8** | **69.8** | **71.2** | **72.8** |
|  | after (ACI) | **72.5** | **72.6** | **73.7** | 79.2 |
| 6 | before | 83.8 | **86.7** | **88.1** | **90.3** |
|  | after (track record, primary) | **87.3** | **90.4** | **92.3** | **92.7** |
|  | after (ACI) | 81.4 | 83.9 | **87.3** | **87.7** |
| 7 | before | **73.0** | **72.0** | **71.2** | **68.5** |
|  | after (track record, primary) | 77.4 | 76.6 | **72.5** | 75.2 |
|  | after (ACI) | 79.3 | 81.7 | 75.8 | **74.8** |
| 8 | before | 78.1 | **74.8** | 75.0 | **73.1** |
|  | after (track record, primary) | 79.9 | 76.8 | 76.7 | 75.6 |
|  | after (ACI) | 77.8 | **74.6** | **71.8** | **67.9** |

## 3. Baselines (study only, not calibrated in the product)

As approved, naive and seasonal naive were run through the same wrapper to show that it is model-agnostic. The
product baseline (`/forecasts/baseline`) is **not** calibrated and is labelled "baseline: no calibration needed".
The naive baseline builds its range from each mandi's own past price changes.

**Naive** already over-covers on average (84–86%). Calibration pulls it towards 80% at longer horizons, but the
spread between datasets stays wide.

| method | statistic | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| before | mean coverage | 84.4 | 85.8 | 86.4 | 85.7 |
|  | range (min–max) | 76.8–87.9 | 73.0–90.5 | 70.3–93.3 | 70.8–93.3 |
|  | mean abs gap from 80 | 5.2 | 7.6 | 8.8 | 8.0 |
|  | datasets within ±5 (of 8) | 4 | 0 | 1 | 1 |
| after (track record, primary) | mean coverage | 83.9 | 84.7 | 84.7 | 82.7 |
|  | range (min–max) | 79.8–89.7 | 74.5–93.5 | 73.5–93.8 | 72.7–91.7 |
|  | mean abs gap from 80 | 3.9 | 6.1 | 6.7 | 6.6 |
|  | datasets within ±5 (of 8) | 5 | 3 | 3 | 3 |
| after (ACI) | mean coverage | 83.4 | 84.6 | 84.6 | 82.2 |
|  | range (min–max) | 80.1–89.9 | 76.0–93.9 | 76.1–93.9 | 70.7–92.8 |
|  | mean abs gap from 80 | 3.4 | 5.6 | 6.4 | 7.2 |
|  | datasets within ±5 (of 8) | 7 | 4 | 3 | 3 |

| dataset (seed) | method | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| 1 | before | 82.9 | **85.7** | **87.5** | **86.6** |
|  | after (track record, primary) | 81.7 | 84.0 | 84.0 | 81.4 |
|  | after (ACI) | 83.4 | **85.5** | **86.0** | 82.4 |
| 2 | before | **87.9** | **87.7** | 85.0 | 81.6 |
|  | after (track record, primary) | **85.1** | 83.2 | 79.1 | **72.7** |
|  | after (ACI) | 82.9 | 82.1 | 77.2 | **70.7** |
| 3 | before | 83.3 | **86.1** | **86.9** | **87.1** |
|  | after (track record, primary) | **85.3** | **89.2** | **90.7** | **91.0** |
|  | after (ACI) | 84.8 | **88.6** | **90.1** | **90.8** |
| 4 | before | **87.9** | **90.5** | **91.8** | **89.0** |
|  | after (track record, primary) | 83.0 | **85.1** | **86.1** | 82.5 |
|  | after (ACI) | 82.4 | 83.8 | **85.5** | 81.8 |
| 5 | before | 84.3 | **86.1** | **86.8** | **87.2** |
|  | after (track record, primary) | 81.8 | 80.9 | 79.5 | 78.5 |
|  | after (ACI) | 81.0 | 80.8 | 79.7 | 75.2 |
| 6 | before | **86.7** | **90.5** | **93.3** | **93.3** |
|  | after (track record, primary) | **89.7** | **93.5** | **93.8** | **91.7** |
|  | after (ACI) | **89.9** | **93.9** | **93.9** | **92.8** |
| 7 | before | **85.2** | **87.2** | **89.6** | **90.3** |
|  | after (track record, primary) | 84.3 | **87.0** | **90.6** | **90.5** |
|  | after (ACI) | 82.7 | **86.0** | **88.5** | **89.6** |
| 8 | before | 76.8 | **73.0** | **70.3** | **70.8** |
|  | after (track record, primary) | 79.8 | **74.5** | **73.5** | **73.0** |
|  | after (ACI) | 80.1 | 76.0 | 76.1 | **74.2** |

**Seasonal naive**

| method | statistic | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| before | mean coverage | 83.2 | 83.3 | 82.8 | 81.8 |
|  | range (min–max) | 78.5–87.7 | 77.6–90.1 | 74.2–90.5 | 72.6–92.0 |
|  | mean abs gap from 80 | 3.7 | 4.9 | 6.2 | 6.6 |
|  | datasets within ±5 (of 8) | 5 | 4 | 3 | 2 |
| after (track record, primary) | mean coverage | 81.3 | 80.5 | 79.1 | 78.3 |
|  | range (min–max) | 76.5–87.0 | 73.4–89.7 | 71.5–88.8 | 70.7–86.4 |
|  | mean abs gap from 80 | 3.4 | 5.1 | 5.6 | 5.6 |
|  | datasets within ±5 (of 8) | 5 | 4 | 4 | 3 |
| after (ACI) | mean coverage | 81.2 | 81.1 | 80.5 | 79.9 |
|  | range (min–max) | 75.4–86.9 | 74.4–88.5 | 72.8–89.7 | 71.1–89.3 |
|  | mean abs gap from 80 | 4.3 | 5.4 | 5.4 | 6.0 |
|  | datasets within ±5 (of 8) | 5 | 3 | 3 | 2 |

| dataset (seed) | method | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| 1 | before | **87.7** | **90.1** | **90.5** | **92.0** |
|  | after (track record, primary) | **87.0** | **89.7** | **88.8** | **86.4** |
|  | after (ACI) | **86.9** | **88.5** | **87.2** | **86.4** |
| 2 | before | **86.8** | **88.0** | **88.9** | **88.3** |
|  | after (track record, primary) | **85.3** | **85.8** | 84.7 | **85.1** |
|  | after (ACI) | **86.1** | **87.5** | **85.9** | **85.9** |
| 3 | before | 78.5 | 78.6 | 76.2 | 75.2 |
|  | after (track record, primary) | 80.6 | 79.7 | 79.2 | 78.0 |
|  | after (ACI) | 82.3 | 81.6 | 80.6 | 81.8 |
| 4 | before | 82.6 | **85.1** | **86.5** | **86.3** |
|  | after (track record, primary) | 76.7 | 76.1 | 75.8 | 76.4 |
|  | after (ACI) | 75.4 | **74.8** | **74.7** | **73.9** |
| 5 | before | 83.2 | 82.6 | 83.6 | 81.6 |
|  | after (track record, primary) | 76.5 | **73.4** | **71.5** | **72.4** |
|  | after (ACI) | 76.8 | 75.9 | 75.7 | **74.9** |
| 6 | before | **86.0** | **87.1** | **86.4** | **85.8** |
|  | after (track record, primary) | **85.9** | **86.9** | **85.6** | 84.2 |
|  | after (ACI) | **86.6** | **88.3** | **89.7** | **89.3** |
| 7 | before | 81.2 | 77.8 | **74.2** | **72.6** |
|  | after (track record, primary) | 80.1 | 75.5 | **72.2** | **70.7** |
|  | after (ACI) | 77.0 | **74.4** | **72.8** | **71.1** |
| 8 | before | 79.4 | 77.6 | 76.1 | **73.0** |
|  | after (track record, primary) | 78.3 | 77.2 | 75.4 | **73.5** |
|  | after (ACI) | 78.4 | 77.6 | 77.4 | 75.9 |

## 4. Cost: pinball loss (Rs/quintal, mean of p10/p50/p90, lower is better)

| method | 1 wk | 2 wk | 3 wk | 4 wk | dataset-horizons where pinball improved vs before (of 32) |
|---|---|---|---|---|---|
| before | 101.9 | 145.1 | 174.5 | 200.9 | – |
| after (track record, primary) | 102.5 | 147.3 | 179.0 | 206.7 | 6 |
| after (ACI) | 102.9 | 150.3 | 186.7 | 216.1 | 5 |

Naive:

| method | 1 wk | 2 wk | 3 wk | 4 wk | dataset-horizons where pinball improved vs before (of 32) |
|---|---|---|---|---|---|
| before | 100.0 | 142.5 | 176.3 | 204.3 | – |
| after (track record, primary) | 100.3 | 144.2 | 179.6 | 209.9 | 6 |
| after (ACI) | 101.0 | 147.3 | 185.0 | 218.1 | 3 |

Seasonal naive:

| method | 1 wk | 2 wk | 3 wk | 4 wk | dataset-horizons where pinball improved vs before (of 32) |
|---|---|---|---|---|---|
| before | 152.9 | 224.4 | 277.4 | 317.7 | – |
| after (track record, primary) | 153.7 | 226.9 | 280.2 | 320.5 | 4 |
| after (ACI) | 154.8 | 231.2 | 287.2 | 330.7 | 3 |

Calibration makes pinball loss slightly worse almost everywhere. A constant widening from last year's track
record helps in the periods where misses happen, which on these datasets are mostly spike episodes. It costs a
little on every other day. The coverage fix is about honest labelling of uncertainty, not accuracy.

## Honest reading

1. **The average is fixed.** The mean coverage of the displayed model's range is within tolerance at every
   horizon after calibration. The 4-week range moves from 74.2% to 78.6%.
2. **The per-dataset spread is not fixed.** Roughly half of the 32 dataset-horizons are still outside ±5 points
   (track record: 17 of 32 within; ACI: 18; before: 15). A global offset cannot correct a model that is too narrow
   in one regime and too wide in another. Fixing that would need conditional calibration, for example by mandi,
   season or recent volatility. That is new modelling, outside B-1's scope, and is logged in the backlog.
3. **ACI is marginally better** at 2–4 weeks (mean absolute gap 3.6 / 5.7 / 6.1 versus 4.7 / 5.9 / 6.4) and costs
   slightly more pinball loss. The pre-registered primary method stays `track_record`. Switching is a one-line
   change (`method = "aci"`) and should be decided on real data, not on this synthetic comparison.
4. **Nothing here says calibration will help on real data.** It is a methodology check. It shows the wrapper
   behaves correctly and does not leak.

## Coverage check in the harness

Every `agripulse_ml.eval.run` now reports coverage against the target, per model and horizon:

- Each run adds `coverage_gap_pct` and `coverage_within_tol` rows to its results.
- `EvalRun.coverage_check()` returns a table with `coverage_pct`, `target_pct`, `gap_pct` and
  `within_tolerance`.
- `agripulse_ml.eval.coverage.assert_coverage(results, models, target, tolerance)` raises `CoverageDrift`.

Target and tolerance come from `config/calibration.toml`.

Tests (`tests/test_calibration.py`):
- A small dataset with an unanticipated spike must trip the check.
- A regression guard requires the committed 8-dataset study to keep mean track-record coverage within tolerance
  at every horizon, and requires the 4-week "before" coverage to be outside it.

## Live status (what users see)

**Storage.** `predict.py` (V1 LightGBM) and `tft/forecast.py` store the raw range in `p10_raw` / `p90_raw` and
the displayed range in `p10` / `p90`. `Forecast.calibration` records one of:
- `applied`
- `not_yet_applicable`
- `none` (switched off by `apply_to_display = false`)

Migration 0008 backfills existing rows as `none` with raw = displayed.

**Offsets.** Offsets come from `serving_offsets()`. It compares this model's own stored forecasts with prices
published before the issue date, keeping synthetic and real rows separate.

**Real data.** Real prices started on 2026-09-25, so for months there will be fewer than 100 scored real
forecasts per horizon. Every real forecast is therefore `not_yet_applicable`, and the UI says "Range not yet
calibrated". This is the expected state, not a fault.

**Where it is labelled.**
- Farmer, buyer and policy forecast charts show a `CalibrationBadge` next to the `ProvenanceBadge`.
- The legend reads "band p10–p90, calibrated / not yet calibrated".
- The API returns `calibration` and `calibration_label` per forecast, and `p10_raw` / `p90_raw` / `calibration`
  per horizon.
- The Admin "V2 results" card has the synthetic study row and a live row showing the newest displayed
  forecasts' status.

## Reproduce

```
python -m agripulse_ml.calibration_study        # about 15 min; writes docs/results/calibration-{synthetic,diagnosis}.csv, -meta.json
pytest tests/test_calibration.py
```

## Known issues

- The wrapper was not run on the ablation's feature-store variants (59–76%) or on TFT (46–59%) in this phase.
  Doing so is a single `apply_to_predictions` call on their harness output.
- Calibration is global per horizon, not conditional. The per-dataset spread remains (see above).
- The live track record uses stored forecasts. If a forecast job is skipped on some days, the record has gaps.
  That only delays `applied`; it does not bias it.
