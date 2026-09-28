# AgriPulse: project guide for Claude

Food-inflation early warning (tomato first) plus farm-to-mandi vehicle tracking. Scope: tomato, Karnataka and neighbouring states.
Full product description: README.md. Data sources: docs/data-sources.md. Forecasting: docs/forecasting.md.

## Layout
- `apps/web`: Next.js + MapLibre dashboards (9 roles), public tracking page
- `apps/driver-pwa`: driver PWA (served by the API at /driver/)
- `services/api` (FastAPI, SQLAlchemy, Alembic), `services/ingest` (jobs), `services/tracking` (GPS, geofences, ETA, simulator)
- `ml`: features, models, walk-forward evaluation, training, prediction
- `infra`: Dockerfiles, OSRM; `docs`: design and results notes

## Commands
- Tests: `pytest` (all must pass before every commit)
- Web: `cd apps/web && npm run build && npm run typecheck`
- Dev stack: see README "Quick start"

## Rules (from V1, still in force)
1. Real vs synthetic: every simulated vehicle or trip has `is_simulated = true` and is labelled "Simulated"; synthetic data is labelled "Synthetic".
2. Time series: walk-forward validation only. Never random train/test splits.
3. Forecast output is quantiles (p10, p50, p90) plus spike probability, never a single point value.
4. Privacy: track a driver only during an active trip, with explicit consent and a visible indicator. Public links are expiring, unguessable tokens exposing only position, ETA and lot status.
5. Never invent API endpoints or resource IDs. Inspect real responses and document them.
6. Secrets only in `.env`; keep `.env.example` current.
7. Every phase ends with passing tests, a README section, and a short "how to verify" list.
8. Plan before code: show a plan and get approval before each phase.

## Current version: V2 (Intelligence). V1 is complete and merged.

## V2 scope
1. Real-data backtest harness and experiment tracking (MLflow)
2. Temporal Fusion Transformer (quantile forecasts, 1-4 weeks)
3. Mandi graph in Neo4j + GNN, compared against the non-graph model
4. Sentinel-2 crop signal: NDVI/EVI, district-level acreage proxy, stress features
5. In-transit tonnage as a model feature
6. Ablation study: (a) prices only, (b) +weather, (c) +satellite, (d) +graph, (e) +in-transit

## NOT in V2
OR-Tools optimizer, scenario simulator, Hindi alerts, polished dashboards (V3).

## New rules for V2
9. Real vs synthetic: models are trained and evaluated on REAL data only.
   Synthetic data may be used only for unit tests and pipeline smoke tests, and any result
   produced with it must be labelled "synthetic" and never appear in the ablation table.
10. Every model is compared against (i) seasonal naive and (ii) the V1 LightGBM quantile
    model, using the SAME walk-forward folds, mandis and metrics
    (pinball loss, MAPE, spike recall, interval coverage).
11. A negative result is a valid result. If a component does not improve the metric,
    report it honestly and do not tune until it "wins".
12. No leakage: every new feature needs a test proving it uses only data available at
    forecast time (satellite acquisition dates, publication lags, graph edges built
    from past data only).
13. Trade-flow edges are ESTIMATES (distance, price correlation, arrivals). Label them
    as estimates in code, docs and UI.
14. Do not break V1: all V1 tests must keep passing. New models sit behind a config flag
    and write to forecasts with a model_name column.
15. Plan first, wait for approval, then build. Summarize built / stubbed / known issues.
