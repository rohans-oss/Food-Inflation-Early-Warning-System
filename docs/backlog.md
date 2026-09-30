# Backlog: known issues outside the current phase

V2 rule 16: anything noticed that isn't the current phase's job goes here, not into that phase's code.
Each item says where it came from and why it's deferred.

## Status after V3-3

### Closed or partly closed in V3-3 (or earlier)

| # | Item | Status |
|---|---|---|
| 1 | ~~React Native driver app~~ | **Closed (superseded by B-2).** Capacitor Android app. Open follow-ups: iOS build, Play Store release, battery-optimisation prompt. |
| 2 | **Kannada native-speaker review** | **Workflow closed, review open.** Every string has a `_review` status; `python -m agripulse_api.i18n_tools export/import` round-trips a CSV for the reviewer (placeholders checked, all-or-nothing). All 83 UI + all alert strings exist in kn and hi, but **0 are native-reviewed** (Kannada and Hindi are machine drafts). Waiting on the reviewer's sheet. |
| 3 | **Mandi-map confirmation** | **Tooling closed, confirmations open.** Admin → Mandi locations: OSM candidates via Nominatim (1 req/s, contact User-Agent, cached), a human picks or drags the point, `verified` is set only by a person and reset when the point moves, every change audited. **All 18 mandis are still `coords_verified = false`** until someone confirms them in a deployment (Nominatim is not reachable from the build sandbox). |
| 4 | Driver can end a trip before the delivery QR is scanned | **Closed.** Driver `end` needs the delivery scan (the driver can still withdraw consent); fleet owner / admin `POST /trips/{id}/close` with an audited reason, which never marks lots delivered (API only; no Fleet-screen button yet). |
| 5 | Refresh tokens can't be individually revoked | **Closed by B-3** (server-side sessions, refresh rotation with reuse detection, revoke per user / per device). |
| 6 | JWT in WebSocket query string | **Closed.** `POST /auth/ws-ticket` → 60 s single-use ticket; web and driver app use it. `?token=` still accepted for older installed driver builds; remove after they update. |
| 7 | Public OSM tile server | **Closed in code.** `apps/web/lib/mapstyle.ts`: self-hosted PMTiles via Caddy `/tiles/`, any MapLibre style URL, or XYZ raster; Admin warns while the public OSM default is in use. The operator still has to cut the extract (`docs/map-tiles.md`). |
| 11 | `ingest.run synthetic` generates up to *today* | **Closed.** Demo history is one draw generated to a pinned horizon (2030-12-31) and cut at `end`, so it is the same whatever day it is loaded. |
| 17 | Delay alerts during a paused / silent phone | **Closed.** No delay alerts while `tracking_paused_at` is set; the paused-tracking alert covers that time. |
| 18 | New Kannada alert copy (`tracking_paused`) is machine-drafted | **Merged into #2.** |
| 19 | Sessions: no "where am I signed in" list | **Closed.** `GET /auth/sessions` + `POST /auth/sessions/{id}/revoke`; web Account page lists devices (the driver app has "sign out everywhere"). |
| 21 | `user_sessions` grows forever | **Closed.** Weekly job `sessions.prune` deletes sessions revoked or idle beyond the refresh lifetime; audit_log is kept. |

### Open, deferred (reason)

Original rows kept for their context; the last column says why each waits.

| # | Item | Why it matters | Notes | Deferred because |
|---|---|---|---|---|
| 8 | Festival flags are approximate calendar windows | Lunar-calendar festivals move ±2 weeks year to year | Replace with a dated festival table | Needs a dated festival table; low impact until real data shows calendar effects. |
| 9 | Multi-pickup trips use one tonnage-weighted point | ETA and geofence are wrong for spread-out FPO pickups | V3-1 optimises the pickup ORDER and keeps shared loads within 20 km, but trips still track one point: multi-stop geofences remain open | Multi-stop geofences are a tracking redesign; wait for real FPO pickup patterns from the field test. |
| 10 | Spike alert threshold (0.5) fires almost never | Spike recall ≈ 0 for every model on synthetic data | Choose the threshold from precision/recall on **real** data | Must be chosen on REAL precision/recall (V3-4 / after ~13 months). |
| 12 | V1 LightGBM intervals under-cover | p10–p90 holds the price 59–73% of the time on the V2 folds (target 80%); the 120-day conformal window often doesn't resemble the next 28 days (docs/tft-results.md) | **Partly addressed by B-1** (docs/calibration-results.md): mean coverage now on target on the 8 datasets; see item 16 for what remains | Real-data item (see #16). |
| 13 | Weather features may hurt the display model | V2-5 ablation (synthetic): LightGBM on prices alone beat prices + weather in 2 of 3 draws (+1.6% to +6.4% mean, noisy) | Re-run `python -m agripulse_ml.ablation --provenance real` once real history is ready; decide the display feature set on REAL data, not synthetic | Real-data item: decide the display feature set on real history. |
| 14 | Calendar features carry one annual harmonic only | V2-5: real NDVI helped synthetic prices only by supplying seasonal shape the single sin/cos pair misses | Add a second harmonic (sin/cos of 2·doy) to `features/store.py _calendar` and to V1 `features/legacy.py`; re-run the ablation | Modelling change; rule 17 forbids model changes in hardening/polish phases. Re-run with the ablation on real data. |
| 15 | Satellite district = 30 km circle, 2 districts only | V2-4 pilot approximation; tomato is ~8% of that cropland | Real district polygons, more districts, and a tomato-specific mask (field boundaries or crop classification) before expecting a tomato signal | Needs district polygons and a tomato mask; out of V3 scope. |
| 16 | Calibration is global per horizon, not conditional | B-1: mean coverage on target, but 15 of 32 dataset-horizons still outside ±5; pinball 0.6–2.9% worse | Conditional calibration (by mandi, season or recent volatility) is new modelling; decide on REAL data. Also run `apply_to_predictions` on the ablation variants and TFT, and choose `track_record` vs `aci` on real data | Real-data item. |
| 20 | No password change / reset | A compromised password can be cut off (revoke + disable) but not changed by the user | Password change (revoking other sessions) + admin reset; email reset needs SMTP | Password change is small, admin reset needs a UI decision, email reset needs SMTP: next hardening pass. |
| 22 | Refresh-reuse grace (30 s) is a window | A thief using a stolen refresh token within 30 s of the owner's refresh also gets tokens | Acceptable for now; tighten to a per-device binding if needed | Acceptable risk for now. |
| 23 | Price impact of oversupply is not modelled | V3-0: the rule's dense-batch "advantage" comes from flooding mandis at no modelled cost; the mandi-room limit (25% of typical arrivals) is an assumption | Estimate price response to arrivals from REAL Agmarknet prices + arrivals; then either price it in the evaluator or re-set the room share from data | Real-data item (Agmarknet prices + arrivals). |
| 24 | Dense batches hit the CP-SAT time limit | 43/80 dense solves FEASIBLE, not proven OPTIMAL, at 10 s | Warm-start from the rule's plan (AddHint), longer limit for batch planning, or symmetry breaking on identical trucks | Performance, not correctness: batch solves are FEASIBLE and violation-free; single-lot API solves are optimal. |
| 25 | Spoilage cap (8%) never binds in Karnataka | V3-0: no trip got close; the limit is inert | Set from measured tomato quality loss (field pilot) before relying on it | Needs field-pilot quality-loss data. |
| 26 | Unshipped lots are scored at Rs 0 | V3-1: plans that ship more lots (dense batches) look better than they are; a lot left at the farm is really sold locally or later | Value unshipped lots at a local/next-day price (from real data) and re-run consolidation_study | Needs real local/next-day prices. |
| 27 | Same-day second sale for return loads | V3-1 assumes the second mandi pays the realised price late in the day | Check late-arrival prices at real mandis (trader data) before trusting return-load value | Needs trader data on late-arrival prices. |
| 28 | Two scenario links are UNSOURCED | V3-2 rainfall chain: share of water need not replaced by irrigation; share of a mandi's arrivals from its own district | Irrigated tomato area / water use (district agriculture office, Minor Irrigation Census); arrival origins from APMC gate records or traders | Needs agronomy / APMC origin data. |
| 29 | Scenario elasticity is RETAIL (RBI CPI) | Mandi (wholesale) prices usually move more, so channel B likely understates shifts | Estimate a wholesale price-arrivals elasticity from real Agmarknet prices + arrivals (also closes backlog 23) | Real-data item (same estimate as #23). |
| 30 | Scenario channel A is not credible yet | The synthetic-trained model gives wrong-sign / erratic answers to drought and supply shocks | Retrain on real data; even then it only knows shocks present in its history. Keep A and B separate | Real-data item. |
| 31 | Alert rule for real spike warnings | V3-4: the pre-registered learned threshold (best F1 on earlier folds) drifts to near always-on when spikes are frequent (67% of days for LightGBM on synthetic data) | Settle the rule BEFORE real events are scored, e.g. a fixed alert budget per mandi or a precision floor; keep 0.5 as the reference | Needs a decision, and real spike frequency |

### Noted in V3-3

- Fixed in passing: the prod compose file had dropped `https://localhost` from CORS, which would have blocked the Android app (Capacitor's origin).

## V3 (by design)

OR-Tools optimizer, scenario simulator, Hindi alerts, multilingual review workflow.
