# PROJECT: AgriPulse - Food-Inflation Early-Warning + Farm-to-Mandi Tracking (India)

## What we are building
A platform that (1) forecasts tomato prices 1-4 weeks ahead as probabilistic ranges
and (2) tracks vehicles carrying produce from farm to mandi live, so farmers, traders,
buyers and policymakers can see supply in motion.

## Current version: V3 (Decisions and Proof). V1 and V2 are merged on main.
V2 is complete (docs/v2-summary.md); Pre-V3 hardening B-1..B-3 is done (calibration, driver app, sessions).

## V3 starting conditions (read before building anything)
- No forecasting model currently beats the naive/"today's price" baseline (see V2 results).
  The optimizer and simulator in V3 are built on the CALIBRATED BASELINE forecast, not
  TFT or the GNN. Advanced models stay wired in behind the existing model_name /
  data_provenance flags and become the default automatically once the readiness monitor
  and a future re-run show they win on real data — do not hardcode them as default now.
  (Concretely: the displayed forecast is `[display] model = "lightgbm_quantile"` (V1), at parity with naive in V2-0,
  with B-1 calibrated ranges. Read forecasts ONLY through the display-model filter.)
- Forecast intervals are now calibrated (conformal prediction, added pre-V3). Use the
  calibrated p10/p50/p90 wherever uncertainty is needed.
- Real price history collection started 2026-09-25 and needs ~13 months for full
  retraining; by V3 there will be some real data but likely still short of that.
- Driver app GPS reliability: B-2 chose option (b). A Capacitor Android app (background location via a foreground
  service) is built in CI but NOT yet field-tested, so its gap reduction is unmeasured; the browser PWA remains the
  fallback (gaps whenever the screen is off, now labelled "location paused"). The optimizer therefore treats
  continuous tracking as best-effort and relies on QR pickup/delivery events, which are reliable, for anything
  decision-critical.
- Session revocation is implemented.

## V3 scope
1. OR-Tools optimizer: best-mandi assignment under cost, capacity, perishability
   constraints, using the calibrated baseline forecast; replaces the V1 rule-based
   recommender as the default, with the old rule-based version kept as a fallback
   and for comparison.
2. Load consolidation and return-load matching.
3. Scenario simulator: rainfall-failure and export-ban scenarios at minimum, shown as
   forecast shifts with uncertainty, clearly labelled as counterfactual estimates.
4. Finished multilingual alerts: Kannada reviewed by a native speaker, Hindi added.
5. Dashboard polish across all 9 roles; readiness and ablation data made clearly visible
   to the Policy and Admin roles.
6. Real backtest on actual historical price spikes using whatever real data exists by
   this point, reported honestly alongside the synthetic backtest, not blended with it.
7. Evaluation package: backtest report, 3-minute demo (if not already done),
   pitch deck, paper-style write-up.
8. Backlog closure: prioritize map/location verification and tile hosting since they
   affect the optimizer's real-world validity; document what's closed vs deferred.

## New rules for V3
21. The optimizer's decision quality is evaluated against the OLD V1 rule-based
    recommender on the SAME scenarios (not against a forecast-accuracy metric) —
    report cost saved, spoilage avoided, and constraint violations avoided.
22. Every scenario-simulator output carries a visible "COUNTERFACTUAL ESTIMATE — not a
    validated causal model" label, the same visibility as the SYNTHETIC data badge.
23. If real data has crossed the readiness threshold for some mandis but not others by
    this point, say so explicitly per module rather than treating the whole system as
    one binary real/synthetic state.
24. The final evaluation package must state, in one place, exactly what is proven on
    real data vs synthetic data vs not yet provable, across the whole project
    (V1 + V2 + V3), not just V3's own components.
25. Plan first, wait for approval, one phase at a time.

Phases: V3-0 optimizer + decision-eval harness -> V3-1 consolidation + return loads -> V3-2 scenario simulator
-> V3-3 multilingual alerts + dashboard polish + backlog closure -> V3-4 real backtest + evaluation package.


## V2 data reality (read this before building anything)
- Real Agmarknet history is just starting to accumulate (started ~2026-09-25) and needs
  about 13 months before it is usable for training. Until then, price-dependent models
  are trained and evaluated on SYNTHETIC data only, and every artifact produced from it
  (model, metric, chart, table) must carry data_provenance = "synthetic" and be labelled
  "SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT" wherever it is shown.
- Satellite (Sentinel-2) and ground-truth (ICRISAT) data ARE real today and don't depend
  on price history. Build and validate the satellite module on real data from the start.
- OSRM distances and the mandi graph's distance edges are real. Only the price-correlation
  and estimated-flow edges depend on price history, so those start synthetic.
- Real in-transit (vehicle) data may exist only from the field test, likely a handful of
  trips. Treat it as too small for a result; use it only to prove the pipeline works.
- One synthetic draw is not evidence (V2-0 finding, docs/backtest-synthetic.md): V1's
  "LightGBM beats naive" held on one draw and not across 8. Report synthetic model
  comparisons across several draws (eval/baseline.py seed_sweep), with mean, range, wins.

## V2 scope
1. Shared walk-forward evaluation harness (works identically on synthetic and real data)
2. Real-data readiness monitor (per mandi, per feature type) on the Admin page
3. Feature store with groups: prices, weather, satellite, graph, transit
4. TFT quantile forecasting
5. Mandi graph in Neo4j + GNN, compared against the non-graph model
6. Sentinel-2 crop signal, validated against real ICRISAT ground truth
7. In-transit tonnage as a feature (pipeline proof only, given data volume)
8. Ablation study, run and reported separately for synthetic and real-so-far data

## NOT in V2
OR-Tools optimizer, scenario simulator, Hindi review, React Native driver rewrite,
Kannada native-speaker review, mandi-map confirmation (all V1 hardening or V3 items —
listed in /docs/backlog.md instead of doing them here).

## V1 scope (still enforced)
- Crop: tomato only for price FORECASTS. Lots / transport / tracking / receipt / payment accept any vegetable in
  config/crops.toml (user request after V3-4); non-forecast crops are ranked by transport cost + spoilage, no price.
  Region: Karnataka + neighbouring states.
- 9 roles, all with real login + RBAC + a working screen:
  1 Farmer, 2 FPO/aggregator, 3 Transporter/driver, 4 Fleet owner,
  5 Mandi trader/commission agent, 6 Bulk buyer, 7 Policy analyst/government,
  8 Lender/insurer, 9 Admin/data ops.
- Multi-tenant: FPOs, fleet owners and organizations have their own data scope.

## Stack
- Backend: FastAPI (Python 3.11), SQLAlchemy 2 + Alembic, Pydantic v2, JWT auth
- DB: PostgreSQL + PostGIS + TimescaleDB, Redis
- Frontend: Next.js (TypeScript) + Tailwind + MapLibre GL
- Driver app: PWA (background GPS, offline buffering) in /apps/driver-pwa
- Routing/ETA: self-hosted OSRM on OpenStreetMap India extract
- ML: pandas, LightGBM (quantile objectives), MLflow, walk-forward validation
- Jobs: Prefect or APScheduler
- Infra: Docker Compose for everything

## Repo layout
/apps/web  /apps/driver-pwa  /services/api  /services/ingest
/services/tracking  /ml  /infra  /docs  /config

## Data sources (all free)
- Agmarknet prices/arrivals via data.gov.in API (key in .env, never commit)
- Open-Meteo (hourly, no key), NASA POWER (daily, no key)
- OpenStreetMap + OSRM for roads
- Driver phone GPS every 5-10 seconds

## Non-negotiable rules
1. Real vs synthetic: every simulated vehicle/trip has is_simulated = true and the UI
   labels it "Simulated". Never present synthetic data as real.
2. Time series: use walk-forward validation only. Never random train/test splits.
3. Forecast output is quantiles (p10, p50, p90) plus spike probability, not a point value.
4. Privacy: track a driver only during an active trip, with explicit consent and a
   visible "tracking on" indicator. Public tracking links must be expiring, unguessable
   tokens that expose only the vehicle position, ETA and lot status.
5. Never invent API endpoints or resource IDs. If unsure about a data.gov.in resource ID
   or field name, call the API, inspect the real response, and write down what you found
   in /docs/data-sources.md.
6. Secrets only in .env; provide .env.example.
7. Every phase ends with: tests passing, a README section, and a short "how to verify" list.
8. Work in small steps. Before writing code for a phase, show me a plan and wait for approval.
   After each phase, summarize what was built, what is stubbed, and known issues.

### New rules for V2
9. Every dataset, model output, chart and table carries an explicit data_provenance
   field: "real", "synthetic", or "real_partial" (real but below the readiness threshold).
   Nothing synthetic may be shown without a visible "SYNTHETIC" label, in the UI, in docs,
   and in any exported report.
10. Every new model is compared against (i) seasonal naive and (ii) V1 LightGBM, on the
    SAME folds, mandis, and metrics, run separately on synthetic and on real-so-far data.
11. A negative result, or "not enough real data yet", is a valid, expected outcome.
    Report it plainly. Do not tune until a result looks good.
12. No leakage: every feature needs a test proving it only uses data available at
    forecast time (satellite acquisition dates, publication lags, graph edges built only
    from data before the fold cutoff).
13. Trade-flow graph edges are ESTIMATES. Label them as estimates in code, docs and UI.
14. Do not break V1: all existing tests keep passing. New models sit behind a config
    flag and write to forecasts with a model_name and data_provenance column.
15. Plan first for each phase, wait for my explicit approval, then build. Actually wait —
    do not continue automatically into the next phase.
16. Any V1 issue you notice but that isn't V2's job goes into /docs/backlog.md, not into
    the current phase's code.

## Pre-V3 hardening (do this before any V3 phase)

Context: V2 found that no model currently beats the naive/"today's price" baseline.
The forecast's 80% interval only contains the true price 59-73% of the time (should be
~80%) — it is overconfident. The driver app stops sending GPS when the phone screen
locks. Individual user logins cannot currently be revoked.

Rules for this work:
17. Do not touch V1 or V2 model logic beyond what's specified below. This is hardening,
    not new modelling. All existing tests must keep passing.
18. Any calibration fix must be validated on the SAME 8 synthetic datasets V2-0 used,
    not a new cherry-picked one. Report coverage before and after, per dataset.
19. The currently-best-performing model for any user-facing forecast is the naive/V1
    baseline (see V2 results). Nothing here should make TFT/GNN the default; that only
    changes when real data says so via the readiness monitor.
20. Plan first, wait for approval, then build one phase at a time.

Phases: B-1 forecast calibration fix -> B-2 driver app (investigate and report, then the user picks the fix)
-> B-3 login/session revocation. Then V3 (optimizer + scenario simulator) against the currently-best model.

## Core tables
users, organizations, roles, lots, shipments, trips, vehicles, gps_points,
geofence_events, mandis, prices, arrivals, weather, forecasts, alerts, data_source_runs
(+ audit_log: every lifecycle transition; V2: model_runs, eval_results)

## Definition of done for V1
- Prices and weather update daily on their own; Admin page shows freshness per source
- Tomato forecast gives 1-4 week ranges with a documented backtest vs baseline
- A real phone drives a real route while a farmer watches live with ETA, geofence events
  and delivery confirmation
- All 9 roles can log in and complete their core task
- 3-minute demo of Tejas's journey from lot creation to delivery

## Conventions in this codebase (read before changing things)
- One Python project (`pyproject.toml`), four packages: `agripulse_api` (services/api),
  `ingest` (services/ingest), `tracking` (services/tracking), `agripulse_ml` (ml).
- Status changes ONLY through `agripulse_api.lifecycle.move()` — it enforces the state
  machine and writes audit_log. Never assign `.status` directly.
- Tenant scoping ONLY through `agripulse_api.scoping` (`lot_filter`, `trip_filter`) and
  `rbac.require(permission)`. Cross-tenant reads return 404, not 403.
- Synthetic data: `source = "synthetic"` on prices/weather/arrivals, `is_simulated` on
  vehicles/trips/gps_points, `trained_on_synthetic` on forecasts. Loaders never mix real
  and synthetic rows.
- Recommender costs: `config/recommender.toml`. Alert copy: `services/api/agripulse_api/i18n/alerts.json`.
- Tests: `pytest` (SQLite in-memory; Postgres-only bits are in the migration and skipped there).
- Migrations: hand-check autogenerated ones; revision ids are 0001, 0002, ...
- Evaluation: ONLY through `agripulse_ml.eval` (`FoldSpec`, `run`) so every model shares folds and
  metrics. Record runs with `eval.tracking.record` (DB) + `log_mlflow`. Provenance values and labels
  live in `agripulse_api.provenance`; readiness thresholds in `config/readiness.toml`.
- UI: forecast / backtest numbers always render with `ProvenanceBadge` (apps/web/components/ui.tsx).
- Features for V2 models: ONLY via `agripulse_ml.features.store.build_table(Inputs..., feature_set)`; train/evaluate
  with `table.fold_spec()` (carries the label publication lag). Lags live in `config/features.toml`. V1 LightGBM keeps
  `features/legacy.py`. Any new feature needs a leakage test in tests/test_feature_store.py.
- Tests set MLFLOW_DISABLE=1 (conftest) so they never write to the real mlruns/.
- Model switches live in `config/models.toml` (`agripulse_api.modelcfg`). Users see ONLY the `[display] model`: every
  forecast read (`forecast_block`, recommender, spike alerts) filters `Forecast.model_name == display_model()`. V2
  models write under their own model_name (TFT: "tft", only when `[tft] write_forecasts = true`).
- Sequence models set `uses_history = True`; the harness passes `history` (rows < cutoff) and `context` (rows < test
  end) and the model must use only rows <= each test row's issue date (tests/test_tft.py tamper test).
- TFT needs the `tft` extra (`pip install -e .[tft]`); tests/test_tft.py skips without it.
- Mandi graph (V2-3): edges ONLY via `agripulse_ml.graph.edges.build_graph(inputs, as_of)` (uses data published by
  `as_of`); config in `config/graph.toml`. Edge provenance: distance = real, price_corr / flow_estimate = the prices'
  provenance; flow_estimate always has is_estimate = true and shows an ESTIMATE badge (`EstimateBadge`). Stored builds
  live in `graph_edges` (`python -m agripulse_ml.graph.build`, weekly job); Neo4j is an optional mirror only.
- Satellite (V2-4): Sentinel-2 via `agripulse_ml.satellite` (config/satellite.toml). Read scale/offset PER SCENE,
  and when `earthsearch:boa_offset_applied` is true the offset is 0 even though raster:bands lists -0.1 (verified on
  raw pixels). NDVI outside [-1, 1] is refused, never stored. Observations live in `satellite_obs`; the `satellite` feature group uses lag
  `sentinel2` from config/features.toml. Ground truth goes through an adapter into validate.py's normalised schema,
  written only after inspecting the real file. The ICRISAT apportioned DLD (1966-2011, no tomato) cannot validate it.
- Transit (V2-5): `transit/features.py`. Snapshot = 00:00 IST of the issue date; ETA from the last GPS fix before it
  or the plan, NEVER from trip.ended_at (it is the future at the snapshot). `tr_tracked = 0` means untracked, not zero
  supply. Synthetic transit is simulated from synthetic arrivals: its gain is built in, never report it as evidence.
- Ablation: `python -m agripulse_ml.ablation` (synthetic) / `--provenance real`; variants differ only in columns.
- V2 findings for the Admin card live in `services/api/agripulse_api/v2_results.py`; update it when a study re-runs.
- Calibration (B-1): `agripulse_ml.calibration` wraps ANY quantile model (config/calibration.toml). Scores use only
  outcomes with known_on < issue date; offsets come from the model's OWN stored forecasts (`serving_offsets`), synthetic
  and real never mixed. Forecast rows keep `p10_raw`/`p90_raw`; `p10`/`p90` are what is displayed; `calibration` is
  applied / not_yet_applicable / none. p50 is never moved. Every eval run reports coverage; `eval.coverage.assert_coverage`
  enforces the tolerance. Displays show `CalibrationBadge`. Re-run `python -m agripulse_ml.calibration_study` when the
  models change (tests guard the committed CSV).
- Driver app (B-2): ONE codebase, `apps/driver-pwa`. `apps/driver-android` is a Capacitor 7 wrapper; its `www/` is a
  generated copy (`scripts/prepare-www.mjs`, API address from `AGRIPULSE_API`), never edited by hand. app.js branches
  on `NATIVE` (Capacitor): native location = background-geolocation foreground service; browser = watchPosition +
  wake lock (re-requested on every return to visible) + `POST /trips/{id}/pause` when hidden. Any path that stops
  tracking goes through `stopTracking()`. A 409 from the server stops GPS (`trackingRefused`). Pause state lives on
  `trips.tracking_paused_at` and is cleared by the next fix recorded after it, by end, or by withdrawn consent. The
  APK builds in CI (.github/workflows/driver-android.yml); this workspace can't reach the Android SDK.
- Sessions (B-3): every sign-in = one `user_sessions` row; tokens carry `sid`. Mint tokens ONLY via
  `agripulse_api.sessions` (`start`, `rotate`, `issue`), never `security.create_*_token` directly. Auth checks go
  through `rbac.get_current_user` (HTTP) and `sessions.user_from_access_token` (WebSockets); long-lived sockets
  re-check (`SESSION_RECHECK_S`). Revoke ONLY via `sessions.revoke(...)`, which writes the audit_log row.
- Decisions (V3-0): `agripulse_api.decisions` (DB-free: model/rule/optimizer/evaluate/sample; service.py = DB adapter).
  ONE cost model (`Problem.net`/`trip_cost`/`spoilage`) for every method; compare methods ONLY through `evaluate()`, and
  in studies score at REALISED prices. `[recommender] default` must equal `decision_study.switch_decision()` on the
  committed CSV (test pins it); re-run the study before changing costs, limits or the default. Mandi overload is a
  violation, never a modelled price drop (that would favour the optimizer by construction).
- Loads (V3-1): shared loads `decisions.loads.optimize_loads`, return loads `decisions.returns.add_return_loads`; the
  evaluator scores a truck's WHOLE day (multi-stop, 2nd trip, per-lot spoilage clock) and must equal V3-0 for one lot
  per truck. CP-SAT ONLY via `optimizer.make_solver` (deterministic work limit, 1 worker: wall-clock limits made
  results load-dependent). Proposals (`load_proposals`) are accepted only through `decisions.proposals.accept`, which
  reuses `routers.lots.make_shipment` / `routers.trips.make_trip`. `[consolidation]`/`[return_loads] enabled` must
  equal `consolidation_study.switch_decision()` on the committed CSV (test pins it).
- Scenarios (V3-2): `agripulse_ml.scenarios` (config/scenarios.toml, every value with a source or "UNSOURCED").
  Channel B `assumptions.py` (sourced chain, low/central/high) and channel A `model.py` (perturb the display model's
  raw inputs, ratios) are NEVER blended. Runs live in `scenario_runs`; nothing writes `forecasts` (test compares it
  byte for byte). Every output carries `label()` = the rule-22 text; UI uses `CounterfactualBadge`.
- i18n (V3-3): languages en / kn / hi in `services/api/agripulse_api/i18n/alerts.json` and `apps/web/lib/messages.json`.
  Every non-English string has a `_review` status (machine / reviewed); only `i18n_tools import` (reviewer CSV,
  placeholders checked, all-or-nothing) sets `reviewed`. tests/test_i18n.py fails on any missing string or placeholder
  mismatch, so a new English string needs kn + hi drafts in the same change.
- Module status (V3-3, rule 23): `agripulse_api.module_status.compute(db)` is the ONE place that says real /
  real_partial / synthetic / not_yet_evaluable per module (Admin + Policy `ModuleStatus`). Derive from readiness data
  and stored provenance, never hardcode "real".
- WebSockets (V3-3): clients get a ticket from `POST /auth/ws-ticket` (60 s, single use) and pass `?ticket=`; never
  put an access token in a URL in new code. Map tiles: ONLY through `apps/web/lib/mapstyle.ts` (docs/map-tiles.md).
- Mandi locations (V3-3): moved only via `PATCH /admin/mandis/{id}` (audited; moving resets `verified`). `verified`
  means a person confirmed it; OSM candidates (`/osm-candidates`, Nominatim) are suggestions, never auto-applied.
- Real backtest (V3-4): `agripulse_ml.real_backtest` (PREREG rules in the module docstring; changing them needs a dated
  note in docs/backtest-real.md, and never after real events were scored). Real and synthetic are separate outputs.
  `docs/final-evaluation.md` is the single rule-24 table: update it with every study re-run.
- Public demo (V3-4): `infra/Dockerfile.demo` bakes a SYNTHETIC SQLite demo; `scripts/demo_start.py` sets
  JWT_SECRET=auto and disables admin unless ADMIN_PASSWORD is set. Never point it at real data. Render services
  `agripulse-api` (https://agripulse-api-0ir4.onrender.com) / `agripulse-demo` (Singapore, free); pushes don't auto-deploy there, trigger manually.
- Synthetic generator `propagation="distance"` is a POSITIVE CONTROL ("PLANTED SIGNAL"); never report it as a result
  about prices. The pinned baseline is `propagation="random"` (the default).
- Receipts: `routers.receipts.issue(db, lot)` at weighing gives `receipt_no` + unguessable `receipt_token`; public
  `GET /public/receipts/{token}` / web `/receipt/[token]` (printable proof: QR scans, GPS count, payment). Payment is
  recorded, never processed. Crops: `agripulse_api.crops` (config/crops.toml), validate with `crops.canonical`.
- LIVE mode (after V3-4, user request): `DATABASE_URL=postgres…` (Neon) at run time switches the demo image to real data
  (`scripts/demo_start.py` → `ingest/live.py` catch-up + scheduler). Daily Agmarknet pull fetches ALL commodities, stores
  the pickable crops (`crops.tracked_feed_names`), notes names in `feed_commodities`. History ONLY via
  `ingest.history` (probe first; backfill uses the probe's findings). Tomato forecasts in LIVE mode come only from
  `live.retrain_if_ready` (real rows, readiness-gated); without them `supply.forecast_available` is false and every crop
  gets `options_without_forecast` (latest real price, value at that price: never called a forecast). Farmers add
  vegetables via `POST /crops` → `crops.resolve` (custom_crops).
- Public-demo wording (user decision 2026-09-30): in the web UI synthetic data is labelled "Sample data" / "Sample"
  (ProvenanceBadge, banner from `/data-status`), never unlabelled. API values, docs, studies and exported reports keep
  "synthetic" and the full rule-9 label. `DATA_MODE=demo` makes the demo image serve its baked sample database even when
  DATABASE_URL points at Postgres.
- Pickup code (2026-10-01, user request): every trip gets a 4-digit `trips.pickup_code` at `make_trip`; only the driver
  (and the driver app) sees it; the farmer enters it at `POST /lots/{id}/confirm-pickup` (5 wrong tries lock it; QR scan
  stays the fallback). All handovers go through `routers.trips.record_pickup`. Public demo: booking starts the demo
  transporter on its own (`bookings.start_demo`, thread; resumed from next-steps after restarts); its truck waits at the
  farm for the farmer's code, and next-steps shows the demo driver's code (`pickup.demo_code`, demo_mode only).
- Public-demo wording (2026-10-01, user decision): no per-item "Simulated" tag in the web UI (`SimBadge` renders only
  with an explicit label); the banner says transporters/drivers/accounts are for demonstration; demo receipts say "Demo
  receipt · not a real sale". `is_simulated` flags in data and APIs are unchanged and still drive every report.
- FPO members (2026-10-01): a member = farmer whose `org_id` is the FPO (or who registered a lot with it). FPO adds
  members / lots via `routers/members.py` (`members:manage`); members added there get an unusable password. Demo members
  come from `seed.seed_demo_members` (CLI `--demo` only, never the test fixture).
- Sign-up (2026-10-01): `auth.register` requires `district` for trader/driver/fleet_owner/fpo/lender/policy; traders
  pick a mandi in that district; drivers sign up ONLY against an invite (`POST /drivers`, inactive user with an
  `@invite.agripulse.local` email, matched by phone) and add their vehicle. Demo autopilot is per SHIPMENT
  (`bookings.start_demo_shipment`), used by farmer bookings and FPO bookings alike.
- Persistent demo (2026-10-01): DATA_MODE=demo + Postgres DATABASE_URL → `scripts/demo_start.prepare_persistent_demo`:
  schema `demo` (search_path demo,public; Alembic `version_table_schema` from DB_SCHEMA so it never touches the live
  tables in public), seed, then `ingest.demo_refresh` extends sample data / forecasts with the baked model and starts
  `bookings.start_demo_traffic` (trucks to the demo trader's mandi + weighed lots awaiting payment, at most every 3 h).
- Payments (2026-10-01): `PaymentIn` + `bookings.payment_details` validate per method (bank: holder, account no.,
  IFSC, bank, branch; upi: UPI ID). `lots.payment_details` keeps the account number ONLY as `account_last4`.
- No site-wide banner (owner's request 2026-10-01): sample data is still labelled per item ("Sample" ProvenanceBadge),
  demo receipts still say so. Landing copy is vegetable-general.
- Real users in the persistent demo (2026-10-01): `agripulse_api.demo` tells demo accounts (@demo.agripulse) and demo
  companies (name ends "(demo)") from real ones. The autopilot runs ONLY for demo fleets, stops at the gate at mandis
  with a real manager (`real_managers`), and auto-pays only demo farmers. JWT_SECRET=auto on Postgres is generated
  once and kept in `app_settings` (`demo_start.stable_jwt_secret`).
- Driver page (2026-10-01): `GET /driver/summary` (trips, km, tonnes, ESTIMATED earnings from config/driver_pay.toml,
  history). Demo driver history from `bookings.seed_driver_history` (once, dates set back); demo traffic includes a
  Hebbal truck so the demo driver has a live trip.
- Driver jobs (2026-10-01): drivers see their company's open farmer bookings (`GET /driver/bookings`, farmer name,
  phone, village, produce, time) and take one (`POST /driver/bookings/{id}/accept` → make_trip with them as driver, trip
  accepted). `trip_out` adds `pickups` (farmer details) for the trip's driver / fleet / FPO. Demo companies wait
  `DEMO_HUMAN_WAIT_S` for a person to accept before a demo driver does.
- Geofence: `reached_pickup` = truck at the farm before the pickup QR; `left_pickup_zone` fires only after pickup scan.
- Direct booking (2026-10-01, user request; rules 30-34 below): `routers/direct.py`. Drivers go online via
  `PUT /driver/availability` (district + mandis in `driver_availability(_mandis)`, a REAL truck of their company);
  farmers `POST /lots/{id}/driver-request` -> `matching_drivers` (online, same district, serves the mandi, truck fits,
  no active trip, seen within 10 min or 8 h with Web Push) -> `trip_request_offers` + `user:{id}` WebSocket message +
  `webpush.send_to_user` + in-app alert. First accept wins (conditional UPDATE on `trip_requests.status`); the trip is
  made by the existing make_shipment / make_trip(channel="direct") and moved to "accepted". Demo accounts and sample
  trucks are refused, the autopilot skips `booking_channel == "direct"`. `trips.booking_channel` = fpo_fleet |
  farmer_company | direct | demo (Admin `/admin/booking-channels`). VAPID keys: env or generated once into
  app_settings (`webpush.vapid_keys`); tests replace `webpush.SENDER`. Requests expire lazily (`expire_due`).
- Drive in the app (2026-10-01): web `POST /auth/handoff-ticket` (drivers, 2 min, single use) -> link
  `/driver/#handoff=<ticket>&trip=<id>` (FRAGMENT, never sent to a server) -> app `POST /auth/handoff` = a NEW session
  for the phone (`sessions.redeem_handoff`). The app's trip view shows the destination (farm until pickup, then the
  mandi) as an OSM embed + Google Maps directions (`renderMap`, redrawn only when the destination changes).
- Mandi gate (2026-10-01): `POST /trader/scan {token}` finds the trip by its delivery QR (any truck, no row to pick),
  runs `trips.confirm_delivery` (the one place arrival is confirmed; also used by `/trips/{id}/scan/delivery`) and
  returns the lots to weigh. `_qr_token` ignores whitespace (the app shows the code in groups of 4) and link prefixes.
  Web `QRScanner` = BarcodeDetector where present, else jsQR on video frames; `onCode` via a ref (re-renders must not
  restart the camera). Trader page: scan -> weigh -> PaymentForm opens for that lot.

## Update: direct farmer-to-driver booking for real-world field testing
30. This flow must work with REAL accounts, REAL district/mandi selection, and REAL phone GPS — not the simulator.
    is_simulated must be false throughout this flow.
31. Do not remove or break the existing FPO-mediated booking flow. This is an ADDITIONAL path.
32. Real-time notification must reach the driver's phone while the app is open (and ideally backgrounded) within a
    few seconds of the farmer's booking request — not require a manual refresh.
33. Farmer and driver see one source of truth for the trip (same trip ID, same status) — no divergent local state.
34. Plan first, wait for approval, then build.
