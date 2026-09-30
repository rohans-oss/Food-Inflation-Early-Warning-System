# Final evaluation: what is proven, on what data (V1 + V2 + V3)

Rule 24: one place that states, for the whole project, what is proven on real data, what only on synthetic data, and
what cannot be proven yet. Written 2026-09-30. Update it whenever a study re-runs; the Admin/Policy "Module status"
card shows the live state of each module.

## 1. How to read this

Every claim gets exactly one label:

| Label | Meaning |
|---|---|
| **REAL** | Measured on real-world data. The size of that data is stated; small real evidence is still real, and still small. |
| **SYNTHETIC ONLY** | Shown on generated data. It proves the method runs and what it does on that data, **not** anything about real tomato markets. SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT. |
| **PIPELINE ONLY** | Runs end to end on real inputs or through the real code paths (tests, emulated phone, demo), but there is too little real data for a result. |
| **NOT YET PROVABLE** | Needs data or people that don't exist yet. The trigger or date is given. |

Scenario-simulator outputs are additionally **COUNTERFACTUAL ESTIMATES — not a validated causal model**, whatever
their data label.

## 2. The one table

| # | Claim | Phase | Label | Key number (provenance) | Evidence | What would prove it / when |
|---|---|---|---|---|---|---|
| 1 | The data.gov.in Agmarknet resource, fields and units are as coded | V1 | **REAL** | 1 real payload, 18 Karnataka tomato rows, 2026-09-25 | `docs/data-sources.md`, `tests/fixtures/` | Quota, page limit and wrapper keys: one run with your key |
| 2 | Cleaning catches bad rows in the real feed | V1 | **REAL** (small) | 1 of 18 rows flagged (Belgaum: min > max, modal outside) | `docs/data-sources.md`, `ingest/cleaning.py` | More days of the daily pull |
| 3 | Prices and weather update daily on their own | V1 | **PIPELINE ONLY** | Scheduler and freshness panel built; not running on any real deployment | README "V1 status" | Run the worker with `DATA_GOV_API_KEY` |
| 4 | 1–4 week forecast ranges (p10/p50/p90) plus spike probability | V1 | **SYNTHETIC ONLY** | Works end to end; trained on synthetic history | `docs/forecasting.md` | Real history: first walk-forward fold 2027-10-24, or immediately after a backfill |
| 5 | V1 LightGBM beats naive | V1→V2-0 | **SYNTHETIC ONLY: refuted** | Held on 1 draw; across 8 draws the mean is −2.2% to +1.8% by horizon, with 3–4 of 8 wins | `docs/backtest-synthetic.md` | Re-run `eval.baseline` / ablation on real data |
| 6 | Shared harness: same folds and metrics for every model | V2-0 | **PIPELINE ONLY** | Tested; used by every study | `ml/agripulse_ml/eval/`, tests | — (method, not a claim about data) |
| 7 | No feature leaks the future | V2-1 | **PIPELINE ONLY** | Planted-leak tests for every feature group | `tests/test_feature_store.py` | — |
| 8 | TFT forecasts better than naive | V2-2 | **SYNTHETIC ONLY: negative** | 15–33% worse (3 draws); raw intervals 46–59% coverage | `docs/tft-results.md` | Real data, once #4 passes its minimums |
| 9 | Mandi-graph features help | V2-3 | **SYNTHETIC ONLY** | +1–3% for LightGBM on 12/12 draw-horizons, only to parity with naive | `docs/graph-results.md` | Real prices (price-correlation edges need them) |
| 10 | The GNN helps | V2-3 | **SYNTHETIC ONLY: negative** | 25–32% worse than naive (pinned); 64–102% worse on one draw | `docs/graph-results.md` | Real data |
| 11 | Graph distance edges | V2-3 | **REAL** (approximate) | 100 edges from real mandi coordinates × road factor 1.3. The coordinates are unverified town centres, and OSRM was not reachable in the studies | `docs/graph-results.md`, `config/graph.toml` | Confirm the 18 mandi points (Admin → Mandi locations); run OSRM |
| 12 | Trade-flow edges | V2-3 | **SYNTHETIC ONLY** (ESTIMATE) | Arbitrage-gravity index, always labelled ESTIMATE | `docs/graph-results.md` | Arrival-origin data (APMC gate records) |
| 13 | The Sentinel-2 pipeline produces sound NDVI | V2-4 | **REAL** | 2,610 observations; out-of-range values refused; a double-applied offset found and fixed | `docs/satellite-results.md` | — |
| 14 | District cropland NDVI tracks tomato area or production | V2-4 | **REAL: negative** | r = 0.60 raw → −0.14 detrended (2 districts, n = 10) | `docs/satellite-results.md` | A tomato-specific mask and more districts (backlog 15) |
| 15 | In-transit tonnage improves forecasts | V2-5 | **NOT YET PROVABLE** | 0 real trips. The synthetic gain is built into the simulation, so it is not evidence | `docs/ablation-results.md` | ≥ 50 real trips over ≥ 90 days per mandi (`config/readiness.toml`) |
| 16 | Which feature groups matter (ablation) | V2-5 | **SYNTHETIC ONLY**; real run "not enough real data" | No group makes LightGBM reliably beat naive | `docs/ablation-results.md`, `docs/results/ablation-real.json` | `python -m agripulse_ml.ablation --provenance real` once history exists |
| 17 | Calibrated 80% ranges hold ~80% of prices | B-1 | **SYNTHETIC ONLY** | Mean 79/78/77/74% → 81/80/79/79%; only 3–5 of 8 datasets within ±5 points per horizon | `docs/calibration-results.md` | Real forecasts with a year of track record |
| 18 | The Android app keeps GPS with the screen locked | B-2 | **PIPELINE ONLY** | Builds in CI; not field-tested; gap reduction unmeasured | `docs/driver-app-investigation.md`, `docs/driver-android.md` | Field test (`docs/field-test.md`) |
| 19 | A real phone on a real route: live ETA, geofences, QR delivery | V1 | **PIPELINE ONLY** | Emulated phone in a real browser; the demo video uses a SIMULATED truck | `tests/test_tracking.py`, `scripts/record_demo.py` | Field test |
| 20 | Logins can be revoked per device; WebSocket tickets | B-3, V3-3 | **PIPELINE ONLY** | Tests (sessions, reuse detection, ticket single use) | `tests/test_sessions.py`, `tests/test_backlog_v33.py` | — (security behaviour, proven by tests) |
| 21 | The optimizer beats the V1 rule | V3-0 | **SYNTHETIC ONLY** | Net value a tie (+0.2%), violations 140 → 0, transport −22% to −34%, 240 simulated days | `docs/optimizer-results.md` | Real lots with sale prices, plus real prices at the unchosen mandis |
| 22 | Shared and return loads save money | V3-1 | **SYNTHETIC ONLY** | Better on 204/240 days, worse on 2; per tonne: transport −11% to −18%, net value +1% to +3% | `docs/optimizer-results.md` | Real FPO batches (field pilot) |
| 23 | Rain failure / export ban → price shift | V3-2 | **COUNTERFACTUAL**, forecasts SYNTHETIC | Chain B: 50% Jun–Jul deficit ≈ +10% (+0.6% to +49%); ban −0.3%. Model A: wrong sign or erratic | `docs/scenario-assumptions.md` | Source the 2 UNSOURCED links; a wholesale elasticity from real data |
| 24 | Alerts in English, Kannada and Hindi | V3-3 | **PIPELINE ONLY** | All strings exist in all 3 languages; 0 of them native-reviewed | `docs/alerts.md`, `i18n_tools status` | Native reviewers' sheets through the review workflow |
| 25 | Mandi locations are correct | V3-3 | **NOT YET PROVABLE** | Tool built; 0 of 18 confirmed | Admin → Mandi locations | A person confirms each mandi in a deployment |
| 26 | Real price spikes can be warned about in time | V3-4 | **NOT YET PROVABLE** | 17 real price rows (one day) | `docs/backtest-real.md` | First fold 2027-10-24, or immediately after an Agmarknet backfill; then ≥ 5 events |
| 27 | Spike warnings at the event level | V3-4 | **SYNTHETIC ONLY** | LightGBM at 0.5: recall 0.40 (0.20–0.58), lead 12 days, 16% of days alerting; naive can't warn | `docs/backtest-real.md` | Real events, with the alert rule settled first (backlog 31) |
| 28 | Every number shows its provenance | V2–V3 | **PIPELINE ONLY** | Badges on every forecast, metric, edge and scenario; module-status card | UI; `module_status.py` | — |
| 29 | Tenancy: organisations can't see each other's data | V1 | **PIPELINE ONLY** | Cross-tenant reads return 404, tested | `tests/test_tracking.py`, `tests/test_loads.py` (cross-tenant cases) | — |
| 30 | The platform is deployable and demo-able | V3-4 | **PIPELINE ONLY** | Public demo on Render with SYNTHETIC data, labelled; resets on restart | `docs/deployment.md` "Public demo" | A pilot deployment (Postgres, worker, backups) |

## 3. Proven on real data

- **The data sources are what the code thinks they are (#1–#2).** One real Agmarknet payload was inspected, and its
  field names and units were recorded. The real feed contains bad rows, and cleaning flags them rather than dropping
  them silently. This is small evidence: one day.
- **Satellite (#13–#14).** 2,610 real Sentinel-2 observations. The pipeline is sound: it refuses impossible NDVI and
  reads scale and offset per scene, because one catalogue lists an offset that was already applied. The scientific
  question has a **negative answer at district scale**: cropland NDVI follows a shared trend, not tomato.
- **Graph distance edges (#11)** are computed from real coordinates, but those coordinates are unverified town
  centres, and the studies used straight-line distance × 1.3 because OSRM wasn't running. Treat them as real inputs
  with a stated approximation.

## 4. Synthetic only

Everything about forecast quality and decision quality: #4, #5, #8–#10, #12, #16, #17, #21, #22 and #27.

- **Why several draws.** One draw of the generator is one random history. V1's own headline claim (#5) held on one
  draw and failed across eight, so every comparison is reported across several draws with the same folds: 8 for
  LightGBM, 3 for the slower TFT and graph models.
- **What the generator is.** Prices are mostly autoregressive noise with spikes weakly tied to excess rain, and
  arrivals follow price. "Nothing beats naive" is partly a property of this data; it is not evidence about real
  markets in either direction.
- **Decisions are scored at realised prices.** Overloading a mandi is counted as a violation, not modelled as a price
  drop. Unshipped lots are scored at ₹0, which flatters consolidation in dense batches; the per-tonne numbers are the
  fair ones.

## 5. Pipeline only (runs; too little real data for a result)

- **Tracking (#19) and the Android app (#18).** The whole lifecycle works: lot → shipment → trip → consent →
  tracking → QR pickup and delivery → weighing → verified history. It has run with an emulated phone and a simulated
  truck. GPS continuity on a locked Android phone is unmeasured.
- **Transit feature (#15).** It works on simulated trips; there are 0 real trips.
- **Daily data (#3), languages (#24), provenance (#28), security (#20, #29), deployment (#30).** Built and tested. They
  become results only with a running worker, native reviewers or real users.

## 6. Not yet provable: readiness timeline

| Item | Trigger | Earliest |
|---|---|---|
| Any real forecast comparison (#4, #5, #8–#10, #16, #17) and the real spike backtest (#26) | ≥ 365 + 28 days of real prices at a mandi | **2027-10-24** from the daily pull alone (if it runs without a break), or **as soon as** older Agmarknet history is backfilled |
| Enough real spike events to report rates (#26) | ≥ 5 scoreable events | Depends on the data. The 2023 national spike would be included in a backfill |
| Alert rule for real warnings (backlog 31) | A decision, made before real events are scored | Any time |
| In-transit feature value (#15) | ≥ 50 real trips over ≥ 90 days per mandi | After a field pilot |
| Android GPS continuity (#18), real-route tracking (#19) | Field test | When you run `docs/field-test.md` |
| Real decision quality (#21, #22) | Real lots with sale prices, and real prices at the unchosen mandis | Field pilot + real price history |
| Mandi locations (#25), distance edges without the approximation (#11) | A person confirms 18 mandis; OSRM running | Any time in a deployment |
| Kannada and Hindi quality (#24) | Native reviewers' sheets | When reviewers are available |
| Scenario chain B without unsourced links (#23) | Irrigated tomato share; arrival origins | Agronomy and APMC data |
| Wholesale price response (#23, backlog 23/29) | Real prices + arrivals | With real history |

## 7. Negative results, as findings

| Finding | Label | Why it matters |
|---|---|---|
| No model reliably beats "today's price" | SYNTHETIC ONLY | The product shows V1 with honest ranges; advanced models stay off until real data says otherwise |
| V1's original "beats naive" claim was one lucky draw | SYNTHETIC ONLY | Justifies multi-draw reporting everywhere |
| TFT and GNN lose clearly | SYNTHETIC ONLY | Complexity was not rewarded; not tuned after seeing results (rule 11) |
| District NDVI doesn't track tomato | **REAL** | Satellite needs a tomato mask, not a bigger model |
| Scenario model channel gives the wrong sign | SYNTHETIC ONLY | Shown next to the sourced chain, never blended |
| Optimizer ties the rule on value | SYNTHETIC ONLY | Its gains are structural (trucks, capacity, no overloading), not forecast skill |
| The learned spike threshold drifts to always-on | SYNTHETIC ONLY | Settle the alert rule before real events are scored |

## 8. Threats to validity

- Synthetic prices are built on assumptions (section 4).
- Mandi coordinates are unverified town centres, and study distances use straight line × 1.3.
- The scenario elasticity is for retail prices (RBI CPI) but is applied to mandi (wholesale) prices; two chain links
  are unsourced.
- Unshipped lots are scored at ₹0; the mandi-room limit (25% of typical arrivals) and the spoilage cap (8%) are
  assumptions.
- Forecast calibration fixes average coverage, not the spread across datasets.
- Kannada and Hindi text is machine-drafted.
- The public demo deployment runs on SYNTHETIC data and resets; it is not evidence of production readiness.

## 9. Reproduce

| Rows | Command |
|---|---|
| #5, #16 | `python -m agripulse_ml.eval.baseline --seeds 1-8` · `python -m agripulse_ml.ablation` (`--provenance real`) |
| #8 | `python -m agripulse_ml.tft.experiment` (needs `pip install -e .[tft]`) |
| #9–#12 | `python -m agripulse_ml.graph.experiment` · `python -m agripulse_ml.graph.build` |
| #13–#14 | `python -m agripulse_ml.satellite.run` (see `docs/satellite.md`) |
| #17 | `python -m agripulse_ml.calibration_study` |
| #21–#22 | `python -m agripulse_ml.decision_study` · `python -m agripulse_ml.consolidation_study` |
| #23 | Policy → Scenario simulator, or `POST /scenarios/run` |
| #24 | `python -m agripulse_api.i18n_tools status` |
| #26–#27 | `python -m agripulse_ml.real_backtest` · `python -m agripulse_ml.real_backtest --provenance synthetic --seeds 1-8` |
| All of them | `pytest` (all tests), plus the Admin/Policy module-status card |
