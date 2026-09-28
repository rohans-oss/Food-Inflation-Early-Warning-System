# Forecasting (V1)

**Output (rule 3):** for each mandi and each horizon of 1–4 weeks, `p10 / p50 / p90` in Rs/quintal, plus the
probability of a **spike** in the next 14 days. It is never a single number.

**Spike definition:** the maximum modal price over the next 14 days is more than `SPIKE_THRESHOLD_PCT`
(default 30%) above today's price.

## Pipeline

```
prices (non-outlier, median across varieties per mandi-day)
  + weather (observed only) + arrivals
  -> daily grid per mandi (forward-fill ≤ 3 days, longer gaps stay empty)
  -> features at day t using data ≤ t only
  -> targets: log(p[t+7h] / p[t]) for h = 1..4, spike label
```

**Features:** price log-changes vs 1/7/14/28 days ago; position vs 7/28-day mean, max, and min; 28-day volatility;
year-over-year; arrivals (7-day vs 56-day); rain 7/30-day and the 30-day anomaly vs the mandi's own past same-month
mean; Tmax 7-day and its anomaly; day-of-year sin/cos; month; approximate festival windows; season (kharif / rabi / summer);
the mandi's price level.

A test (`tests/test_ml.py::test_features_do_not_look_ahead`) recomputes all features after deleting the future
and asserts nothing changes.

## Models

| Model | What it is |
|---|---|
| `naive` | Last price; interval = empirical spread of past h-week changes |
| `seasonal_naive` | Last year's move over the same window, applied to today's price; residual-quantile interval |
| `lightgbm_quantile` | One LightGBM quantile regressor per horizon × quantile on log-ratio targets; quantiles sorted to prevent crossing; intervals **conformalised** on the most recent 120 training days; spike probability from a LightGBM classifier (unweighted, so probabilities stay calibrated) |

## Walk-forward backtest (rule 2)

`python -m agripulse_ml.train [--synthetic] [--folds 8]`

- Folds step forward 28 days at a time. A fold starting at T trains only on rows whose **4-week target date** is before T,
  so no label from the test period leaks into training.
- Metrics are computed in Rs/quintal: pinball loss (mean over the 3 quantiles), MAPE of p50, p10–p90 coverage
  (target 80%), and spike recall / precision / Brier.
- The report is written to `ml/artifacts/backtest.json` and shown on the Admin → Model page.

### Current numbers — SYNTHETIC data only

Run on 28/09/2026 over the generated 2022–2026 history, 18 mandis, 8 folds (Jan–Aug 2026).
**These are not real results.** They show the pipeline works end to end.

| Horizon | LightGBM pinball | Naive pinball | LightGBM vs naive | LightGBM p10–p90 coverage | Naive coverage |
|---|---|---|---|---|---|
| 1 wk | 84 | 85 | −1.6% | 76% | 86% |
| 2 wk | 103 | 109 | −5.7% | 77% | 89% |
| 3 wk | 114 | 122 | −6.6% | 76% | 91% |
| 4 wk | 120 | 131 | −8.2% | 74% | 93% |

Spike recall at a 0.5 alert threshold: LightGBM 0.15, baselines 0.

An honest reading:

- LightGBM beats naive on pinball, and the gap grows with the horizon. At 1 week it's a near-tie.
- Its intervals are still slightly **too narrow** (74–77% vs the 80% target), even after conformal calibration.
  Naive intervals are too wide. Neither is calibrated yet; tightening this is V3 work.
- Spike recall is low. A 0.5 threshold on a calibrated probability rarely fires when the base rate is around 20%.
  Pick the alert threshold from the precision/recall trade-off on **real** data, not synthetic.
- Seasonal naive is poor on this data, because the synthetic series has weak year-to-year repetition.

**Re-run on real data** as soon as you have Agmarknet history. If LightGBM does not beat naive there, report that.

## Serving

`python -m agripulse_ml.predict` (also scheduled daily at 20:30 IST) writes the latest forecast for every mandi whose
most recent observed price is within 7 days of the newest date. It reads `GET /forecasts/{mandi_id}` and `GET /forecasts`.
If `MLFLOW_TRACKING_URI` is set and `pip install -e .[mlflow]` is done, training runs are logged to MLflow.
