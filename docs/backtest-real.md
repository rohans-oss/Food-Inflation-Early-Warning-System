# Spike backtest: real data vs synthetic (V3-4)

> **Real data: not enough yet. No real number is reported below, on purpose.**
> The synthetic section is **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT**. The two are never blended.

Question: would the forecast users see (V1 LightGBM, B-1 calibrated ranges) have warned about real tomato price
spikes, how early, and how often would it cry wolf, compared with naive and seasonal naive?

Command: `python -m agripulse_ml.real_backtest` (real rows in `DATABASE_URL`) and
`python -m agripulse_ml.real_backtest --provenance synthetic --seeds 1-8`. Code: `ml/agripulse_ml/real_backtest.py`.
Tests: `tests/test_real_backtest.py`.

## Pre-registered rules

These were written before any real rows could be scored. There are still none to score. One rule was revised after
the first synthetic sanity run; the change is marked.

| Rule | Value |
|---|---|
| Spike day | The V1 label: the highest price in the next 14 days is more than 30% above today's |
| Event | A run of spike days at one mandi; runs less than 7 days apart are one event |
| Rise date R | The first day after the run starts on which the price is more than 30% above the run's first-day price |
| Scoreable | The model issued a forecast on every day of [R−14, R−1] at that mandi |
| Detected, lead time | At least one alert (spike probability ≥ threshold) in [R−14, R−1]; lead = R − first alert, in days (1–14) |
| False alarms | Alert days outside every event's [R−14, R−1] at that mandi, per mandi-year. Also reported: alert precision (share of alert days inside a window) and alert-day share (an always-on alarm is 100%) |
| Thresholds | (a) fixed 0.5, the V1 setting; (b) per fold, the best day-level F1 on earlier folds' forecasts whose labels were public before that fold (0.5 when there is no such history) |
| Minimum evidence | Fewer than 5 scoreable events: counts only, all rates withheld. Less than one fold (365 days of training + 28 days of targets): "not enough real data" and nothing else |
| Folds | Real: every 28-day fold from day 365 on, all scored. Synthetic: V2-0's final 8 folds scored, 13 earlier folds (a year) build the calibration and threshold track record, as in B-1; seeds 1–8 |
| Models | V1 LightGBM (display model), naive, seasonal naive, through the shared harness (`agripulse_ml.eval`). TFT and GNN are not run: they lost to naive on synthetic data (V2), and would only be re-run once this backtest passes its minimums on real data |
| Label lag | 1 day (Agmarknet publishes a day's prices by the next day) |

**Revised on 2026-09-30, before any real row existed:** false alarms were first counted as alert *episodes*. The
first synthetic run showed an always-on alarm then scored zero false alarms, since one endless episode overlaps every
event. They are now counted as alert *days*, with alert-day share reported beside them.

## Real data: result

| | |
|---|---|
| Real price rows | **17**: one day, 2026-09-25, at 17 Karnataka mandis. The real 18-row payload in `tests/fixtures/`; one Belgaum row is flagged as an outlier because its min is above its max |
| Status | **not_enough_real_data**: "Not enough history for a walk-forward backtest: 2026-09-25..2026-09-25 (need > 365 days + 28 days of targets)" |
| Earliest first fold | **2027-10-24**, if the daily Agmarknet pull runs without a break from 2026-09-25. After that, the 5-event minimum decides |
| Advanced models | Not run |

Output: `docs/results/backtest-real.json`. The Admin and Policy "Module status" card shows the same thing live as
**Real spike backtest: not yet evaluable**, with the longest real history and the date a first fold becomes
possible.

**Why no real spikes are reported.** Well-known real episodes exist, such as the July–August 2023 national tomato spike.
But the only real prices this system holds are one day from its own feed. Older Agmarknet history is published: the
data.gov.in "Variety-wise Daily Market Prices Data of Commodity" dataset and Agmarknet's price reports. It was not
reachable from the build workspace, and it has not been downloaded yet. Once it is, the backtest needs no code
changes:

```bash
python -m ingest.run backfill <downloaded file> --state Karnataka   # inspect the file first (rule 5)
python -m agripulse_ml.real_backtest
```

Weather history (NASA POWER) can be backfilled for the same years. Arrival tonnage depends on what the download
contains.

## Synthetic data: what the method shows (SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT)

8 synthetic draws × V2-0's 8 folds × 18 mandis: about 11 mandi-years and 101–126 scoreable events per draw.
Output: `docs/results/backtest-events-synthetic.csv`.

| Model | Threshold | Recall (mean, range) | Median lead | Alert days | Alert precision | False-alarm days / mandi-yr |
|---|---|---|---|---|---|---|
| LightGBM (V1) | fixed 0.5 | **0.40** (0.20–0.58) | 12 days | 16% | **0.52** (0.41–0.62) | 29 |
| LightGBM (V1) | earlier-folds F1 | 0.92 (0.71–1.00) | 14 days | 67% | 0.45 | 135 |
| naive | fixed 0.5 | 0.00 | – | 0% | – | 0 |
| naive | earlier-folds F1 | 0.97 (0.75–1.00) | 14 days | 97% | 0.40 | 214 |
| seasonal naive | same as naive (both use a constant spike probability, the training base rate) | | | | | |

**Reading:**

1. **Naive can't warn at all.** Its spike probability is the historical base rate, the same every day. At 0.5 it never
   alerts. With a learned threshold it alerts almost every day, so its "recall" means nothing. An always-on alarm's
   precision is the share of days that fall before a spike: 0.36–0.44 here, because the synthetic generator spikes
   very often (about 10 events per mandi-year).
2. **LightGBM carries some spike information on this data.** At 0.5 it alerts on 16% of days and catches 40% of events,
   a median 12 days ahead. Its alert precision (0.52) beats always-on in 7 of 8 draws, by 0.12 on average.
   - This is different from V2-0's "spike recall ≈ 0". That was day-level recall on one draw (seed 7); this is
     event-level recall, where any alert in the 14 days before counts, over seeds 1–8.
   - It says nothing about real markets. The synthetic spikes were generated from rainfall, with timing only weakly
     tied to it.
3. **The pre-registered learned threshold (F1) is a poor rule for frequent events.** When about 40% of days sit
   before a spike, F1 rewards alerting almost always: 67% of days for LightGBM, 97% for naive. This result is
   reported as it is, not tuned away. Backlog 31 asks for the alert rule for real data (for example, a fixed alert
   budget per mandi) to be settled before real events are scored.
4. **Calibrated ranges hold on these folds.** LightGBM p10–p90 coverage is 79/78/77/74% raw and 81/80/79/79%
   calibrated (1–4 weeks, target 80), matching B-1. Pinball loss is at parity with naive, as in V2-0: 102/147/179/207
   calibrated vs naive 100/143/176/204.

## What this does and does not show

- **Shows:** the real-data path works end to end and labels itself. `tests/test_real_backtest.py` runs it on planted
  real-source history and checks that it returns results stamped real / real_partial. Event scoring, lead time and
  the minimum gate are tested. The learned threshold is tamper-tested to use only labels public before its fold.
- **Does not show:** anything about real tomato spikes. That needs real history: either backfilled Agmarknet data or
  about 13 months of the daily feed.
