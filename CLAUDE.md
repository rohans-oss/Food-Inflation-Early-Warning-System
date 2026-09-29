# PROJECT: AgriPulse - Food-Inflation Early-Warning + Farm-to-Mandi Tracking (India)

## What we are building
A platform that (1) forecasts tomato prices 1-4 weeks ahead as probabilistic ranges
and (2) tracks vehicles carrying produce from farm to mandi live, so farmers, traders,
buyers and policymakers can see supply in motion.

## Current version: V2 (Intelligence). V1 is merged on main.

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
- Crop: tomato only. Region: Karnataka + neighbouring states.
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
