# Backtest vs baseline (V1)

> **Status: pipeline check on synthetic data only. This is not evidence of real-world accuracy.**
> No real Agmarknet history has been loaded yet. Replace this page with the real run as soon as the backfill is in
> (see "How to produce the real report" below).

## Run recorded here

| | |
|---|---|
| Command | `python -m ingest.run synthetic && python -m agripulse_ml.train --synthetic --folds 6` |
| Data | **synthetic**, 2022-01-01 → 2026-09-28, 18 Karnataka mandis, 31,137 mandi-days |
| Validation | walk-forward, 6 folds × 28 days (first test fold starts 2026-03-16), train only on rows whose 4-week target date is before the fold |
| Spike | max price in the next 14 days > 30% above today; alert when probability ≥ 0.5 |

### Pinball loss (Rs/quintal, mean over p10/p50/p90, lower is better)

| Model | +1 wk | +2 wk | +3 wk | +4 wk |
|---|---|---|---|---|
| naive (last price + empirical spread) | 88.3 | 112.1 | 123.8 | 131.7 |
| seasonal naive | 183.4 | 259.8 | 290.1 | 309.1 |
| **LightGBM quantile** | **87.8** | **110.5** | 124.8 | 132.0 |
| LightGBM vs naive | +0.6% | +1.5% | −0.8% | −0.2% |

### MAPE of p50 and p10–p90 coverage (target 80%)

| Model | MAPE +1 / +4 wk | Coverage +1 / +4 wk |
|---|---|---|
| naive | 12.3% / 17.3% | 86% / 94% (too wide) |
| seasonal naive | 24.9% / 44.0% | 76% / 77% |
| LightGBM quantile | 12.2% / 16.4% | 77% / 70% (slightly too narrow at 3–4 wk) |

### Spike detection (14 days ahead, 573 spike days in the test folds)

| Model | Recall | Precision | Brier |
|---|---|---|---|
| naive / seasonal naive | 0.00 | – | 0.157 |
| LightGBM classifier | 0.16 | 0.42 | 0.157 |

## Reading it honestly

- On synthetic data LightGBM **ties** the naive baseline on pinball loss. It is slightly better at 1–2 weeks and slightly worse at 3–4 weeks.
  That is expected, because the synthetic generator is mostly a seasonal random walk with little signal in the features.
- LightGBM's MAPE is a little lower and its intervals are sharper. They are also a little under-covered at 3–4 weeks, so the
  conformal calibration window should be revisited on real data.
- Spike recall of 16% at a 0.5 threshold is low. The baselines, though, cannot flag spikes at all. The threshold is a product
  decision (alert fatigue vs missed spikes): report the recall/precision curve on real data before choosing it.
- The seasonal-naive baseline is poor here because synthetic seasonality varies year to year. Keep it in the real report anyway.

## How to produce the real report

1. Get a data.gov.in key and set `DATA_GOV_API_KEY`. Start the worker (`docker compose up -d worker`) so the daily pull begins.
2. Backfill history from Agmarknet report downloads: `python -m ingest.run backfill <file.csv> --state Karnataka` (repeat per file).
3. Load weather history: `python -m ingest.run nasa_power --days 1500`.
4. `python -m agripulse_ml.train --folds 8` (no `--synthetic`: it refuses to mix, and fails if there is no real data).
5. The report lands in `ml/artifacts/backtest.json` and on Admin → Model performance. Copy the tables here, **including any
   horizon where LightGBM loses to naive**, and remove the status banner.

After that, the worker retrains every Sunday at 02:15 IST (`retrain_weekly`, real data only). A failure shows under Admin → Failed jobs.
