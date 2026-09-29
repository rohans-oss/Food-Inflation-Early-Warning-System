# V2 synthetic baseline

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Every number on this page comes from the synthetic
> price generator (`ml/agripulse_ml/synthetic.py`), not from Agmarknet. It says how the *pipeline* and the
> *comparison method* behave. It says nothing about real tomato prices. `data_provenance = "synthetic"` on every row.

Reproduce: `python -m agripulse_ml.eval.baseline --seeds 1-8` (about 7 minutes on CPU). Raw long-format results:
[`results/backtest-synthetic.csv`](results/backtest-synthetic.csv) (pinned draw, per mandi) and
[`results/backtest-synthetic-seeds.csv`](results/backtest-synthetic-seeds.csv) (8 draws, pooled).

## Setup (the V2 standard every later model is compared on)

| | |
|---|---|
| Harness | `agripulse_ml.eval` (one fold definition, one metric set) |
| Data | synthetic, generator seed 7, 2022-01-01 → 2026-09-25, 18 mandis, ~12% missing days |
| Feature set | `prices+weather` (V1 features: price history + arrivals, weather, calendar) |
| Folds | 8 walk-forward folds of 28 days, cutoffs 2026-01-16 … 2026-07-31; each trains only on rows whose 4-week target date is before the cutoff |
| Models | seasonal naive, naive (last price + empirical change quantiles), V1 LightGBM quantile |
| Metrics | pinball loss (mean of p10/p50/p90, Rs/quintal, lower is better), MAPE of p50, p10–p90 coverage (target 80%), spike recall / precision / false-alarm rate at 0.5 |

## 1. Does the harness reproduce V1? Yes, exactly

Rerunning V1's two backtests through the new harness on **the same data V1 used** (the generator run up to the day of
each V1 run):

| Run | LightGBM vs naive, pinball, 1 / 2 / 3 / 4 wk |
|---|---|
| V1, 8 folds, data to 2026-09-28 | +1.6 / +5.7 / +6.6 / +8.2 % |
| V2 harness, same data | +1.6 / +5.6 / +6.6 / +8.2 % |
| V1, 3 folds, data to 2026-09-29 | +1.4 / +6.7 / +12.0 / +11.6 % |
| V2 harness, same data | +1.5 / +6.7 / +12.0 / +11.6 % |

Differences are rounding only. A second check ran the old V1 evaluator and the new harness side by side on a
fresh dataset: identical folds, zero metric differences. **The harness is a faithful replacement.**

## 2. Does V1's conclusion hold? No

V1 reported that LightGBM beats naive by "1–2% at 1 week, 8–12% at 3–4 weeks". The runs above reproduce that, but
V1's generator always ran up to *today*. A different end date changes the length of every random array, so each V1
run used a **different random synthetic history**. On the pinned V2 dataset the picture flips:

| Horizon | Seasonal naive | Naive | LightGBM (V1) | LightGBM vs naive | LightGBM p10–p90 coverage | LightGBM MAPE |
|---|---|---|---|---|---|---|
| 1 wk | 153.3 | **90.4** | 98.2 | −8.7% | 73.0% | 13.6% |
| 2 wk | 228.0 | **114.8** | 123.5 | −7.6% | 72.0% | 17.2% |
| 3 wk | 301.1 | **129.8** | 131.3 | −1.2% | 71.2% | 17.8% |
| 4 wk | 359.4 | 141.9 | **138.5** | +2.4% | 68.5% | 18.4% |

| Model | Spike events | Recall | Precision | False-alarm rate | Brier |
|---|---|---|---|---|---|
| seasonal naive | 796 | 0.000 | – | 0.000 | 0.160 |
| naive | 796 | 0.000 | – | 0.000 | 0.160 |
| LightGBM (V1) | 796 | 0.073 | 0.264 | 0.050 | 0.165 |

To tell a real edge from luck, the same comparison was run on **8 independent synthetic draws** (seeds 1–8, same
dates, same folds):

| Seed | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| 1 | +2.7 | +5.1 | +5.9 | +7.0 |
| 2 | −0.5 | −4.8 | −6.6 | −6.0 |
| 3 | −6.8 | −8.5 | −5.2 | −4.5 |
| 4 | +2.2 | +6.8 | +4.6 | −2.2 |
| 5 | −7.5 | −17.9 | −12.3 | −11.1 |
| 6 | −2.3 | −2.8 | +2.3 | +7.6 |
| 7 (pinned) | −8.7 | −7.6 | −1.2 | +2.4 |
| 8 | +3.3 | +12.8 | +19.6 | +21.6 |
| **mean** | **−2.2** | **−2.1** | **+0.9** | **+1.8** |
| LightGBM wins | 3 / 8 | 3 / 8 | 4 / 8 | 4 / 8 |

(% by which LightGBM's pinball loss is lower than naive's; positive = LightGBM better.)

**Verdict: on this synthetic data, V1 LightGBM has no reliable advantage over naive at any horizon.** The mean effect is
within ±2.2% and the sign flips from draw to draw. Seasonal naive is much worse than both everywhere (the generator has
weak year-to-year repetition). LightGBM's p10–p90 intervals average 74–79% coverage across draws, slightly narrow.
Spike recall at a 0.5 threshold is near zero for every model.

Likely reason (not proven): the generator is mostly AR noise, with spikes whose timing is only weakly tied to rainfall.
There is little for a feature model to learn beyond "the price tomorrow is near the price today". That's a property
of the synthetic data, not evidence about real markets. Real Agmarknet prices may reward LightGBM more, or less.

## 3. What this changes for the rest of V2

1. **Synthetic comparisons are reported across several draws** (mean, range and win count), never one. A
   single-draw difference of ±10% is inside the noise shown above. `seed_sweep()` in `eval/baseline.py` does this.
2. The V1 README and `docs/forecasting.md` numbers are corrected to point here.
3. The honest baseline for TFT / GNN (V2-2, V2-3) is: *naive is as good as V1 LightGBM on synthetic data.* A new model
   "winning" on synthetic data means little unless it wins consistently across draws, and even then only shows the
   pipeline can learn the generator's structure.
4. The real test is unchanged: rerun `python -m agripulse_ml.train` once the readiness monitor shows ≥ 365 days of real
   prices (projected 2027-09-24 for mandis whose collection started 2026-09-25).
