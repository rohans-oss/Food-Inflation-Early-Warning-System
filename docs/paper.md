# AgriPulse: forecasting and tracking tomato supply, and reporting honestly what the data cannot yet show

*Rohan S, B.Tech CSE (Digital Technology), Atria University, Bengaluru. Working paper, V3-4, 30 September 2026.*

> **Provenance convention.** Every quantitative result below is tagged **[REAL]**, **[SYNTHETIC]** (a methodology
> demo on generated data, not a result about real markets) or **[COUNTERFACTUAL]** (an assumption-based estimate, not
> a validated causal model). Untagged statements describe the system, not a measurement.

## Abstract

Tomato prices in India are volatile: in 2023 the all-India average retail price went from ₹24.68/kg in January to
₹108.92/kg by 11 July (Department of Consumer Affairs data, as reported by CNBC). AgriPulse is an open-source
platform for Karnataka. It does four things:
- forecasts mandi prices 1–4 weeks ahead as p10 / p50 / p90 ranges with a spike probability;
- tracks farm-to-mandi trips with consent-based phone GPS and QR checkpoints;
- recommends where and how to ship, using a constraint optimizer;
- runs labelled what-if scenarios.

Real mandi price history only began accumulating on 2026-09-25. So most model evaluation is on synthetic data, reported
across eight independent draws with identical walk-forward folds.

The main findings are negative:
- no forecasting model (LightGBM, a Temporal Fusion Transformer, a graph neural network) reliably beats a naive
  "today's price" baseline [SYNTHETIC];
- real Sentinel-2 cropland NDVI does not track tomato area beyond a shared trend [REAL].

The decision layer does add value independent of forecast skill [SYNTHETIC]:
- a CP-SAT optimizer ties the rule-based recommender on net value (+0.2%), removes all 140 mandi-overload
  violations, and cuts transport cost by 22–34%;
- shared and return loads improve on it on 204 of 240 days.

A real-data spike backtest is implemented and pre-registered but cannot run yet: there are 17 real price rows [REAL].
We argue that the evaluation infrastructure, provenance labelling and pre-registration are the durable contribution,
and we give the date or trigger at which each open claim becomes testable.

## 1. Introduction

Three gaps motivate the work:
1. **Timing.** Official mandi prices (Agmarknet) are published after the market day, and arrivals after that.
2. **Form.** Available forecasts, where they exist, are single numbers, so a user cannot tell a confident forecast
   from a guess.
3. **Supply in motion.** Produce already on trucks is invisible to policy until it is reported as an arrival.

AgriPulse addresses all three for one crop (tomato) and one region (Karnataka and neighbouring states). It has nine
user roles: farmer, FPO, driver, fleet owner, trader, bulk buyer, policy analyst, lender/insurer and admin.

The project was built in versions:
- **V1:** the platform and a first forecaster.
- **V2:** better models and new data sources, each measured against simple baselines.
- **Pre-V3 hardening:** calibration, the driver app, revocable sessions.
- **V3:** decisions, scenarios, languages and evaluation.

This paper reports all of them, with the provenance of every number.

## 2. System

**Data.**
- Agmarknet daily prices, via the data.gov.in resource "Current Daily Price of Various Commodities". It serves only the
  current day, so the daily pull is the history. Every raw payload is archived.
- Weather from Open-Meteo (hourly, with forecasts archived as issued) and NASA POWER (daily, backfillable).
- Sentinel-2 L2A from Earth Search.
- Road distances from OSRM on OpenStreetMap.
- Phone GPS every 5–10 seconds during a trip.

A cleaning layer flags bad rows instead of dropping them silently. On the first real day, a Belgaum row had a minimum
price above its maximum [REAL].

**Platform.**
- FastAPI, PostgreSQL/PostGIS/TimescaleDB, Next.js with MapLibre, and a driver PWA.
- Since B-2, a Capacitor Android wrapper with a foreground location service.
- Every lifecycle transition goes through one state machine that writes an audit log.
- Tenant scoping returns 404 across organisations.
- Tracking runs only during an active trip, after explicit consent, with a visible indicator.
- Public tracking links are unguessable, expiring tokens that expose only position, ETA and lot status.
- Sessions are server-side and revocable per device. WebSockets use 60-second single-use tickets.

**Provenance.**
- Every price, weather row, trip, forecast, metric, graph edge and scenario output carries `data_provenance`: real,
  real_partial (real but below a readiness threshold) or synthetic. Simulated vehicles carry `is_simulated`.
- The UI renders a badge wherever a number appears.
- A readiness monitor reports, per mandi and data type, how much real history exists against configured thresholds.
- A per-module status card reports real / real_partial / synthetic / not_yet_evaluable for each module (V3-3).

## 3. Forecasting and evaluation methods

**Target.** Log price ratio at 1, 2, 3 and 4 weeks ahead, predicted as the 10th, 50th and 90th quantiles, plus the
probability of a spike. A spike means the maximum price in the next 14 days exceeds today's by more than 30%.

**Baselines.**
- Naive: today's price, with the empirical spread of past h-week changes as its interval.
- Seasonal naive: last year's change over the same window.

**Models.**
- V1 LightGBM quantile regression on price history, arrivals, weather and calendar features, with a 120-day
  conformal adjustment.
- A Temporal Fusion Transformer.
- LightGBM with mandi-graph features. The graph edges are road distance [REAL], price correlation, and an estimated
  trade flow that is labelled as an estimate.
- A graph neural network.

**Harness.**
- One walk-forward fold definition is shared by every model: 365 days minimum training, 28-day test steps, and
  training labels restricted to those published before the cutoff.
- One metric set: pinball loss, p50 MAPE, p10–p90 coverage and spike metrics.
- Every feature group has a leakage test that plants a future value and checks it cannot be seen.

**Synthetic data.** A generator produces prices, arrivals and weather for 18 real mandis. Spikes are driven weakly by
excess rain, and arrivals follow price. After V2-0 found that V1's advantage over naive held on one draw and not
others, every synthetic comparison has been reported across 8 draws (seeds 1–8) with mean, range and wins.

**Calibration (B-1).** A model-agnostic rolling conformal step uses the model's own out-of-sample errors over the last
365 days. Scores are used only once their outcome is public. p50 is never moved. The method and target were
pre-registered before any calibrated result was seen.

## 4. Decision and scenario methods

**Optimizer (V3-0).**
- CP-SAT (OR-Tools) assigns lots to mandis and trucks. It respects vehicle capacity, a spoilage cap (V1's formula:
  trip duration × temperature × crop sensitivity) and mandi room (25% of typical arrivals, an assumption).
- It maximises forecast p50 price × quantity − transport − expected spoilage. A p10 (risk-averse) variant is also
  reported.
- It uses a deterministic work limit with one worker, because wall-clock limits made results depend on machine load.

**Scoring.**
- Decisions are compared with the V1 rule (best net value, nearest free truck) through one evaluator, on the same
  simulated batches.
- They are scored at *realised* prices, not forecasts.
- Mandi overload is counted as a violation, not modelled as a price drop. Modelling it as a price drop would favour the
  optimizer by construction.
- The switch rule was pre-registered and is pinned by a test against the committed results.

**Loads (V3-1).**
- Shared truckloads (up to 4 lots within 20 km, pickup order optimised) and return loads (a second trip after a
  delivery).
- The evaluator scores a truck's whole day, with a per-lot spoilage clock.

**Scenarios (V3-2).** Two channels, never blended:
- **(B) An assumption chain:** supply shock → price through a sourced elasticity (−0.721, RBI working paper
  08/2024, ±1 SE), FAO's tomato yield response to water (ky 1.05), and a harvest lag. Two links are explicitly
  UNSOURCED with wide ranges.
- **(A) The display model's own response** when its raw inputs are perturbed.

Scenario runs are stored separately. A test proves the forecasts table is byte-identical before and after.

**Real spike backtest (V3-4).** Event-level rules, fixed before any real row could be scored:
- An event is a run of spike days at a mandi.
- Detection is an alert in the 14 days before the rise; lead time is measured in days.
- False alarms are alert days outside every event window.
- Thresholds: fixed 0.5, and a threshold learned only from earlier folds.
- At least 5 events are required before any rate is reported.

## 5. Results

### 5.1 Forecasting [SYNTHETIC; 8 draws for LightGBM, 3 for TFT and the graph models]

Pinball loss relative to naive (positive = better than naive):

| Model | 1 wk | 4 wk | Note |
|---|---|---|---|
| V1 LightGBM | −2.2% (mean) | +1.8% (mean) | Wins 3–4 of 8 draws per horizon: no reliable edge |
| Temporal Fusion Transformer | 15–33% worse | | 3 draws; raw intervals cover 46–59% (target 80) |
| LightGBM + graph features | +1–3% vs LightGBM alone | | 12 of 12 draw-horizon pairs, but only to parity with naive (−1.1% to +0.6%) |
| Graph neural network | 25–32% worse | | Unstable: 64–102% worse on one draw |

**Positive control.** When the generator plants a distance-based lead-lag signal, the result is only a weak pass.
Message passing starts to help the GNN, but far mandis gain no more than near ones. The control is under-powered
(the signal exists only at spike onsets), not a proof either way.

**Ablation (V2-5).** No feature group makes LightGBM reliably beat naive:
- removing weather helped in 2 of 3 draws;
- the transit feature's gain is built into the simulation;
- the satellite gain is seasonality.

### 5.2 Calibration [SYNTHETIC, the same 8 datasets]

- Mean p10–p90 coverage went from 79/78/77/74% to 81/80/79/79% (1–4 weeks; target 80).
- Only 3–5 of 8 datasets are within ±5 points per horizon after calibration: the average is fixed, the spread is not.
- Pinball loss rose 0.6–2.9%.

### 5.3 Satellite [REAL]

- 2,610 Sentinel-2 observations were collected for the pilot districts.
- Cropland NDVI correlates with tomato area at r = 0.60 raw, but at −0.14 once both series are detrended
  (2 districts, n = 10).
- Tomato is about 8% of that cropland, so a district-level signal cannot isolate it.
- The ICRISAT district database (1966–2011, no tomato) cannot validate it. Ground truth came from Horticultural
  Statistics at a Glance.

### 5.4 Decisions [SYNTHETIC; 240 simulated batches]

| | V1 rule | Optimizer | + shared & return loads |
|---|---|---|---|
| Mean realised net value per day | ₹16,17,042 | ₹16,20,608 (+0.2%) | Better than the optimizer on 204/240 days, worse on 2 |
| Mandi-overload violations | 140 | 0 | 0 |
| Transport cost | — | −22% to −34% | Per tonne shipped −11% to −18% |

- In dense batches the optimizer earns 1.1% less than the rule, because it refuses to overfill mandis and overfilling
  is not charged.
- Dense-batch consolidation gains (+38% to +47%) come mostly from shipping more lots, and an unshipped lot is scored
  at ₹0. That overstates the gain; per tonne, the net-value gain is +1% to +3%.

### 5.5 Scenarios [COUNTERFACTUAL; forecasts SYNTHETIC]

**50% rain deficit, June–July, Kolar and Chikkaballapur.**
- Channel B: about +10% p50 (range +0.6% to +49%).
- Channel A: 0%, because the window is outside the model's 30-day view.

**Export ban at the national export share (0.47% of production: WITS 96.8 kt exports against PIB 208.19 lakh t
production).**
- Channel B: −0.3%.
- Channel A: the wrong sign.

For a drought over the last 30 days, channel A flips sign across horizons. We present A only as a sensitivity of a
synthetic-trained model.

### 5.6 Spike warnings

**[REAL]** Not evaluable: 17 real price rows (one day). The earliest possible first fold is 2027-10-24 without a
historical backfill.

**[SYNTHETIC, 8 draws, about 11 mandi-years and 101–126 events per draw]**
- At a 0.5 threshold, LightGBM catches 40% of events (range 20–58%), a median of 12 days ahead. It alerts on 16% of
  days with alert precision 0.52, above the always-on baseline in 7 of 8 draws.
- Naive cannot warn at all: its probability is constant.
- The pre-registered learned threshold (best F1 on earlier folds) drifts towards always-on (67% of days), because
  events are frequent. We report this rather than tuning it, and flag the alert rule as a decision to make before
  real events are scored.

## 6. Discussion

**What the negative results mean.**
- The synthetic generator is mostly autoregressive noise with weakly timed spikes, so "no model beats naive" is partly
  a property of the data. It is not evidence about real markets in either direction.
- What it does establish: the harness, fold discipline and multi-draw reporting catch a false positive that a single
  backtest would have shipped (V1's original claim).
- The satellite result is on real data, and it is negative at the district scale.

**Where the value is today.** The decision layer's gains come from structure: truck assignment, capacity, consolidation
and refusing to overload a mandi. They do not come from forecast skill. So they should survive a switch to real prices
better than any forecasting claim, though that too is unproven.

**Threats to validity.**
- Synthetic prices are built on assumptions.
- Mandi coordinates are town centroids until a person confirms them (a tool for this exists; none are confirmed yet).
- Study distances are approximate (straight line × 1.3).
- The scenario elasticity is for retail prices, applied to wholesale prices.
- Unshipped lots are scored at zero.
- Two scenario links are unsourced.
- Kannada and Hindi text is machine-drafted.
- The Android app's GPS continuity is unmeasured.

## 7. What is proven, and when the rest can be

The full claim-by-claim table is in `docs/final-evaluation.md`. In short:
- **Real:** road distances and distance edges; the satellite pipeline and its negative finding; ingest and cleaning
  on the real feed; the tracking lifecycle, as exercised by tests and the demo.
- **Synthetic only:** all forecast comparisons, calibration, and the decision and load gains.
- **Not yet provable:** real spike warnings, the value of in-transit tonnage, Android GPS gaps, and decisions scored on
  real sales.

These become testable with a historical Agmarknet backfill (immediately), a field pilot, or about 13 months of daily
collection (around October 2027). None of them needs code changes.

## 8. Conclusion

AgriPulse is a working platform with honest instruments. Its forecasts are not yet better than today's price. Its
decisions are measurably better organised. Every number it shows says where it came from. The next result that
matters, real spike warnings, is one backfill away.

## References

- CNBC (13 July 2023). "India's tomato prices surge over 300%, prompting thieves and turmoil", citing Department of
  Consumer Affairs data. <https://www.cnbc.com/2023/07/13/indias-tomato-prices-surge-over-300percent-prompting-thieves-and-turmoil.html>
- Roy, Gupta, Wardhan, Sarkar, Tewari, Bansal, Bhatia, Gulati (2024). *Vegetables Inflation in India: A Study of
  Tomato, Onion and Potato (TOP).* RBI Working Paper (DEPR) 08/2024.
- FAO. Crop information: tomato (yield response factor ky).
- World Bank WITS / UN Comtrade: India exports, HS 070200 (2023).
- Press Information Bureau, Ministry of Agriculture & Farmers Welfare: 2023-24 horticulture production, first advance
  estimate.
- Gibbs, I. and Candès, E. (2021). Adaptive conformal inference under distribution shift. NeurIPS.
- Lim, B., Arık, S. Ö., Loeff, N., Pfister, T. (2021). Temporal Fusion Transformers for interpretable multi-horizon
  time series forecasting. *International Journal of Forecasting.*
- Google OR-Tools, CP-SAT solver.

Project documents with the evidence behind each section: `docs/backtest-synthetic.md`, `tft-results.md`,
`graph-results.md`, `satellite-results.md`, `ablation-results.md`, `calibration-results.md`, `optimizer-results.md`,
`scenario-assumptions.md`, `backtest-real.md`.
