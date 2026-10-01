# AgriPulse — Food-Inflation Early-Warning System

Forecasts tomato prices 1–4 weeks ahead as **ranges** (p10 / p50 / p90 + spike probability) and tracks produce vehicles
**live from farm to mandi**, so supply in motion becomes a leading price signal. Nine user roles, one platform.

**Status: V1 (platform), V2 (intelligence), pre-V3 hardening and V3 (decisions and proof) are code complete.**

- **What is proven on real data, what is only shown on SYNTHETIC data, and what can't be proven yet:**
  [docs/final-evaluation.md](docs/final-evaluation.md). In short, no forecast model beats "today's price" yet
  (synthetic), and real price history started on 2026-09-25.
- **Write-ups:** [paper](docs/paper.md) · [pitch outline](docs/pitch-outline.md) · [open backlog](docs/backlog.md).
- **Still needs you in the real world:**
  - the field test with a real phone;
  - native Kannada and Hindi review;
  - confirming the 18 mandi locations;
  - real price history (a backfill, or about 13 months of the daily pull).
- **Public demo** (SYNTHETIC data, resets on restart): <https://agripulse-demo.onrender.com> ·
  [how it's deployed](docs/deployment.md#public-demo-render-0).

```
Agmarknet · Open-Meteo · NASA POWER ──> ingest (APScheduler) ──> PostgreSQL + PostGIS + TimescaleDB
Driver PWA ──WebSocket / HTTP batch──> tracking (geofence, ETA, OSRM) ──> Redis pub/sub ──> live maps
                                        │
                   LightGBM quantile forecaster (walk-forward) ──> forecasts ──> best-mandi rules, spike alerts
                                        │
                   FastAPI (JWT + RBAC + tenants) ──> Next.js dashboards for 9 roles · public tracking link
```

| Path | What |
|---|---|
| `services/api` | FastAPI: auth, lots, shipments, trips, prices, forecasts, alerts, role views, Alembic migrations |
| `services/ingest` | Agmarknet / Open-Meteo / NASA POWER jobs, cleaning, scheduler |
| `services/tracking` | GPS ingest, geofences, ETA, OSRM client, live hub, simulator |
| `ml` | features, models, walk-forward backtest, training, daily prediction, synthetic data |
| `apps/web` | Next.js 15 + Tailwind + MapLibre: one screen per role, public `/track/{token}` |
| `apps/driver-pwa` | Driver app (served by the API at `/driver/`) |
| `config/recommender.toml` | transport and spoilage cost assumptions |
| `infra` | Dockerfiles (API, public demo), Caddyfile, OSRM prep, tiles |
| `scripts` | demo recorder, demo start |
| `docs` | [final evaluation](docs/final-evaluation.md) · [data sources](docs/data-sources.md) · [forecasting](docs/forecasting.md) · [alerts](docs/alerts.md) · [routing](docs/routing.md) · [deployment](docs/deployment.md) · [field test + demo script](docs/field-test.md) |

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env                     # set JWT_SECRET
alembic upgrade head
python -m agripulse_api.seed --demo      # one demo login per role, password agripulse-demo
python -m ingest.run synthetic           # optional: LABELLED synthetic history so the demo has forecasts
python -m agripulse_ml.train --synthetic --folds 3 && python -m agripulse_ml.predict
uvicorn agripulse_api.main:app --reload --app-dir services/api          # http://localhost:8000/docs
cd apps/web && npm install && npm run dev                                 # http://localhost:3000
pytest                                                                    # 47 tests
```

Docker: `docker compose up -d --build`. Production with HTTPS: see [docs/deployment.md](docs/deployment.md).

Demo logins (after `--demo`): `farmer@`, `fpo@`, `driver@`, `fleet@`, `trader@`, `buyer@`, `policy@`, `lender@` `demo.agripulse`;
admin `admin@agripulse.local` / `agripulse-admin` (SQLite dev only).

---

## Phase 0 — Foundation, auth, 9 roles

JWT access (30 min) + refresh (14 days) tokens, typed so one can't be used as the other. Nine roles with one permission
map (`rbac.py`). FPOs, fleets, buyers, lenders and government bodies are **tenants**; row-level scoping lives in
`scoping.py` and cross-tenant reads return 404. All 16 core tables plus `audit_log`, with Alembic migrations 0001–0003. On
Postgres the migration adds PostGIS geography columns + GiST indexes and turns `gps_points` into a Timescale hypertable.

**How to verify**
- `pytest tests/test_auth_rbac.py tests/test_lifecycle.py -k "role or refresh or register or admin"`
- Sign in as each demo user: each lands on its own screen; opening another role's URL bounces you home.
- `GET /admin/users` as the farmer → 403. A second FPO sees none of the first FPO's lots (`test_tenant_isolation`).

## Phase 1–2 — Data ingestion and forecasting

- **Agmarknet** (`data.gov.in` resource `9ef84268-…`): paged pull per state, raw payload archived daily, idempotent upsert,
  CSV/JSON backfill. Verified against real payloads; a real 25/09/2026 Karnataka pull is the test fixture. Details and
  what still needs checking with your key: [docs/data-sources.md](docs/data-sources.md).
- **Cleaning:** name normalisation onto canonical mandis, `DD/MM/YYYY` dates, outlier flags (min > max, modal outside the
  range, more than 5× the 30-day median). Unknown markets are auto-created without coordinates.
- **Weather:** Open-Meteo (observed + 16-day forecast) and NASA POWER, with `-999` stored as NULL.
- **Forecasting:** no-look-ahead features; naive, seasonal naive and LightGBM quantile models; conformal interval
  calibration; spike classifier. **Walk-forward backtest only.** [docs/forecasting.md](docs/forecasting.md) has the results table.

**How to verify**
- `pytest tests/test_ingest.py tests/test_ml.py` (includes a test that deleting future data changes no feature).
- With a key: `python -m ingest.run agmarknet`, then open `data/raw/agmarknet/<date>.json` and the Admin freshness panel.
- `python -m agripulse_ml.train --synthetic` prints LightGBM vs naive pinball and coverage per horizon.
- `GET /forecasts/{mandi_id}` returns 4 horizons with `p10 ≤ p50 ≤ p90` + `spike_prob_14d`. `GET /forecasts/baseline` gives the naive version.

**Honest result so far — SYNTHETIC, METHODOLOGY DEMO:** across 8 synthetic draws, V1 LightGBM has **no reliable edge
over naive** (mean −2.2% / −2.1% / +0.9% / +1.8% pinball at 1–4 weeks; it wins only 3–4 of 8 draws). The "1–2% / 8–12%
better" figure first reported for V1 reproduces exactly on the draw it came from, but it was one favourable draw.
Details: [docs/backtest-synthetic.md](docs/backtest-synthetic.md). Nothing here says anything about real tomato prices.

## Phase 6 — Live vehicle tracking

Driver PWA (login, trips, accept/decline, consent, visible **TRACKING ON** bar, wake lock, IndexedDB offline buffer,
QR scan). GPS over WebSocket or HTTP batch, accepted **only** during an in-progress trip with consent, idempotent on
(trip, timestamp). Geofences: `picked_up`, `left_pickup_zone`, `reached_mandi`, `unexpected_stop` (> 30 min, including a
phone that goes silent). OSRM routes with a flagged straight-line fallback. Expiring, unguessable public links expose
only position, ETA and lot status. The simulator sends synthetic trucks (`is_simulated = true`, badge everywhere)
through the same ingest path.

**How to verify**
- `pytest tests/test_tracking.py`: consent rule, replay de-duplication, stop / silent-phone detection, delay alerts,
  expired links, public socket payload, simulator flags, implausible speeds.
- Admin → Vehicle simulator → Start, then watch the Fleet map.
- Real phone: [docs/field-test.md](docs/field-test.md). **Phones only allow GPS on HTTPS pages.**

> **Background GPS warning.** Browsers pause geolocation when the screen locks or the driver switches app. The PWA
> buffers through network dead zones, but not through a locked screen. If the field test shows drivers locking phones,
> move the driver app to React Native with a background-location service. The server API stays the same.

## Phase 7 + 9 — Lots, shipments, in-transit supply, recommender, alerts

Lifecycle `registered → grouped → in_transit → at_mandi → delivered → paid` enforced by one state machine
(`lifecycle.py`); every transition goes to `audit_log` (`GET /lots/{id}/history`). QR chain of custody at pickup and
delivery. The trader board shows in-transit tonnage vs typical daily arrivals ("expected today vs normal"), display only
in V1. Best mandi: `net = p50 × qty − road km × rate × tons − spoilage`, ranked, with p10–p90 net ranges and a warning
when ranges overlap. Costs are in `config/recommender.toml`. Alerts: spike, pickup, incoming, delay, stop, arrival,
delivery. In-app + live, email via SMTP, SMS via a webhook adapter; English + Kannada (`i18n/alerts.json`).

**How to verify**
- `pytest tests/test_roles.py tests/test_lifecycle.py`: recommender math, state machine, audit trail, alert routing, Kannada.
- The whole journey through the real UI + PWA in a browser: `python tests/e2e/journey_ui.py` (instructions in the file).

## Web app — all 9 roles

| Role | Screen |
|---|---|
| Farmer | lots · register a lot on a map · nearby prices · forecast vs baseline · best mandi · live vehicle + ETA + events · pickup QR · delivery confirmation · share with lender |
| FPO | group lots into shipments · book fleet · farmer-wise tonnage · payouts |
| Driver | link + QR to the PWA · trip list |
| Fleet owner | live fleet map · assign vehicle + driver · utilization · trip history · vehicles · approve drivers |
| Trader | incoming board + map · expected today vs normal · delivery QR scan · weigh + price |
| Buyer | watched mandis · forecasts · expected tonnes next 3 days · price alerts |
| Policy | spike-risk map (colour + label) sized by tonnes in transit · district rollup · arrival anomalies |
| Lender | verified chain per lot · explainable trip-reliability score |
| Admin | freshness per source · failed jobs · data quality · model performance · users · simulator |

**How to verify:** `cd apps/web && npm run build` (type-checks every page), then sign in as each role.

---

## V2-0: evaluation harness, readiness monitor, provenance labels

V2 (Intelligence) is being built phase by phase. Until about 13 months of real Agmarknet history exists, price models
run on synthetic data, and every output says so (`data_provenance`: `real` / `real_partial` / `synthetic`).

- **One harness** (`ml/agripulse_ml/eval/`): one walk-forward fold definition, pinball / MAPE / coverage / spike recall /
  false-alarm rate, results as a long table (`model_name, feature_set, horizon, mandi, data_provenance, metric_name,
  metric_value`), recorded to the DB (`model_runs`, `eval_results`) and MLflow. V1 training now runs through it and
  produces identical numbers.
- **Readiness monitor** (`config/readiness.toml`, `GET /admin/data-readiness`, Admin page): days of *real* history per
  mandi and data type, missing %, a ready flag, and a projected ready date.
- **Labels everywhere:** red SYNTHETIC — METHODOLOGY DEMO / amber REAL — LIMITED HISTORY / green REAL on every forecast,
  chart (legend, tooltip, table view), recommender, policy and admin number.
- **Synthetic baseline:** [docs/backtest-synthetic.md](docs/backtest-synthetic.md). Finding: V1's "LightGBM beats naive"
  was one favourable synthetic draw; across 8 draws there is no reliable edge.
- Out-of-scope issues: [docs/backlog.md](docs/backlog.md).

**How to verify**
- `pytest` → 66 tests (47 V1 + 19 V2-0), including a migration round-trip and hand-computed metrics.
- `python -m agripulse_ml.eval.baseline --seeds 1-8` reproduces every number in `docs/backtest-synthetic.md`.
- Admin page: "Real-data readiness" shows 0/18 ready and a projected date; "Evaluation runs" lists runs with provenance.
- Any forecast screen (farmer, buyer, policy, admin): the red SYNTHETIC — METHODOLOGY DEMO badge is visible, including
  in the chart tooltip and table view.

## V2-1: feature store

Group-based, publication-lag-aware training tables ([docs/feature-store.md](docs/feature-store.md)):
`prices` and `weather` built, `calendar` always on, and `satellite` / `graph` / `transit` registered but refusing until
their phases. Columns are tagged static / known-future / past-only (TFT needs this split). Every source has a publication
lag (prices 1 day, NASA POWER 3), and labels are only used for training once published. Weather forecasts are now
archived *as issued* (`weather_forecasts` table, migration 0005), so known-future weather can be backtested on real
data once the archive is about 13 months old.

**How to verify**
- `pytest tests/test_feature_store.py`: 18 tests, including per-group leakage tests (see the doc for the planted-leak check).
- `python -m agripulse_ml.features.build --feature-set prices+weather --provenance synthetic` prints a data card
  and writes Parquet; `--feature-set prices+transit` refuses with "built in V2-5".
- Admin → Real-data readiness now has a "Forecast archive" column.

## V2-2: Temporal Fusion Transformer (TFT)

A TFT quantile forecaster (pytorch-forecasting, CPU) on the V2-1 feature table, compared with seasonal naive, naive
and the V1 LightGBM model on the **same folds, rows and metrics** (4 folds × 3 synthetic draws). Results, including
where TFT loses: [docs/tft-results.md](docs/tft-results.md) — SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.

**Result: negative.** Averaged over 3 draws, TFT's pinball loss is 15–33% worse than naive, and it wins only 1 of 3
draws (at 3–4 weeks). Its raw intervals hold the price 46–59% of the time (target 80%). Calibration helps on one draw
only. LightGBM also doesn't beat naive (−1.7% to −3.1%). TFT stays off the display path.

- Inputs: mandi as a static input; calendar as the only known-future input; prices, arrivals, observed weather and
  the weather forecast *as issued* as past-only inputs (feeding actual future weather would be a perfect-forecast leak).
- Two variants from one fit per fold: `tft_raw`, and `tft_cqr` (same split-conformal step as V1 LightGBM on a held-out
  120-day window). Spike probability is derived from the quantiles and is labelled an approximation.
- Switches in `config/models.toml`: `[tft] data_provenance = "real"` runs the same code on real rows (readiness stamps
  each mandi real / real_partial); `[tft] write_forecasts = true` writes forecasts as `model_name = "tft"`. Users only
  ever see the `[display] model` (default `lightgbm_quantile`); every forecast read filters by it.
- Install: `pip install -e .[tft]` (torch, lightning, pytorch-forecasting). Tests skip TFT without it.

**How to verify**
- `pytest tests/test_tft.py`: 6 tests (leakage tamper test, harness history contract, quantile ordering + CQR,
  config-only switch to real data with real / real_partial labels, forecast flag + display-model filter).
- `python -m agripulse_ml.tft.experiment --folds 1 --seeds 7` (~5–25 min on 2 cores) prints the comparison and writes
  `docs/results/tft-synthetic.csv`; the full run (no flags) is ~80 min on 2 cores and reproduces docs/tft-results.md.
- `python -m agripulse_ml.tft.forecast` does nothing while `write_forecasts = false`; with `--force` it writes
  `tft` rows, and the farmer / buyer screens still show the LightGBM forecast.

## V2-3: mandi graph + GNN

A mandi graph with three edge types, each labelled with its source: **distance** (real road km; straight line × 1.3 until
OSRM runs), **price correlation** (from price history, so synthetic for now), and **trade flow** (an **ESTIMATE**:
a relative index from an arbitrage-gravity rule, never tonnes). It is used three ways:

- **`graph` feature group** in the feature store: neighbours' recent price moves. It is rebuilt every 28 days, each
  snapshot only from data published by its date, and leakage-tested against planted leaks.
- **GNN** (plain torch: GRU + 2 graph-convolution layers) compared with the same network without edges, with LightGBM
  with and without graph features, and with naive / seasonal naive. Same folds, 3 draws, run on the pinned generator
  and on a **PLANTED SIGNAL** positive control where spikes spread by distance.
- **Product:** `graph_edges` table (migration 0006), weekly `python -m agripulse_ml.graph.build`,
  `GET /graph/mandi/{id}/neighbours`, a "Connected mandis" card on the policy and admin pages, and an optional Neo4j
  mirror (`docker compose --profile graph up -d neo4j`).

Results: [docs/graph-results.md](docs/graph-results.md) — SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.
**Graph features help LightGBM by 1–3% on every draw and horizon, which brings it level with naive, not ahead.**
On the pinned generator distance carries no information, so this is not evidence that geography matters. The GNN
loses to naive by 10–32% and is unstable. The positive control is a weak pass: message passing helps once a spatial
signal is planted, but far mandis gain no more than near ones. Nothing user-facing changes; LightGBM on
`prices+weather` stays the display model.

**How to verify**
- `pytest tests/test_graph.py tests/test_feature_store.py`:
  - GNN tamper test (no mandi's rows after t reach t)
  - message passing moves only the graph model
  - snapshot-before-cutoff
  - graph leakage tests (each checked against a planted leak)
  - edge labels
  - API (roles, 404, before-first-build)
  - Neo4j statements (parameterised; FLOW_ESTIMATE carries the ESTIMATE label)
  - config-only switch to real data
- `python -m agripulse_ml.graph.build` then, as policy or admin, open the policy page → "Connected mandis". Distance
  rows say REAL (approx.), correlation rows SYNTHETIC, flow rows SYNTHETIC + ESTIMATE.
- `python -m agripulse_ml.graph.experiment --generators random --seeds 7 --folds 1` (~2 min); the full run (~22 min)
  reproduces docs/graph-results.md. Admin → Evaluation runs marks planted-signal runs "PLANTED SIGNAL".

## V2-4: Sentinel-2 crop signal (REAL data)

Sentinel-2 cropland NDVI for Kolar and Chikkaballapur, 2018–2026 (2,610 scene × district rows). It was fetched on a
machine that could reach Earth Search, then validated against the district tomato statistics printed in
Horticultural Statistics at a Glance (2015-16 to 2023-24). Results: [docs/satellite-results.md](docs/satellite-results.md).

**Result: negative for tomato.** On 6 pre-planned comparisons, district-wide cropland greenness does not track
tomato area or production. The one correlation that passes (area vs mean NDVI, r = 0.60) disappears once the shared
2018→2022 trend is removed (r = −0.14). Tomato is only about 8% of the cropland. The signal itself is sound: NDVI
0.15–0.88, lowest in March–May and highest after the monsoon, and lowest in the 2019 drought year. It is available
as the `satellite` feature group (provenance real).

Found on the real data and fixed:
- items without band assets
- duplicate reprocessed scenes
- overlapping tiles on the same day
- an offset applied twice. Earth Search's `raster:bands` says −0.1 on items whose pixels are already corrected;
  this was verified on raw values.

Details are in docs/data-sources.md.

**How to verify**
- `pytest tests/test_satellite.py`: 14 tests, including:
  - offset-flag handling
  - refusing NDVI outside [−1, 1]
  - version-1 file migration
  - the ground-truth adapter
  - the detrended check
  - lag and truncation leakage, both checked against a planted leak
- `python -m agripulse_ml.satellite.validate data/satellite/observations.csv` reproduces the table in
  docs/satellite-results.md from the committed pilot file.
- `python -m agripulse_ml.satellite.load data/satellite/observations.csv` loads it into `satellite_obs` (idempotent).

## V2-5: in-transit feature, ablation, V2 wrap-up

- **`transit` feature group:** tonnes on the road towards each mandi, as known at 00:00 IST of the issue date.
  - Only trips already started count.
  - ETA comes from the last GPS fix before that time (or the plan), never from the actual arrival.
  - Real trips are labelled REAL — LIMITED HISTORY until the readiness threshold; simulated trips are SYNTHETIC.
- **Ablation** ([docs/ablation-results.md](docs/ablation-results.md)): LightGBM with each group added to prices +
  weather, same folds, 3 synthetic draws.
  - Graph +1–3% on every draw.
  - Transit +1–3%, but that gain is built in by simulation.
  - Satellite +0.3–0.7%; this is seasonality, since real NDVI can't explain synthetic prices.
  - Removing weather helped in 2 of 3 draws.
  - **Nothing reliably beats naive.**
  - Real-so-far: **not enough real data yet** (the same command re-runs it when prices mature).
- **Wrap-up:**
  - [docs/v2-summary.md](docs/v2-summary.md) collects every V2 result.
  - Admin → "V2 results" card lists each study with its data label.
  - Backlog items 13–15 record what to re-check on real data.

**How to verify**
- `pytest tests/test_transit.py tests/test_ablation.py`: 11 tests.
  - snapshot at midnight IST
  - ETA never uses the actual arrival (checked against a planted leak)
  - GPS after the snapshot ignored
  - truncation
  - untracked ≠ zero supply
  - real vs simulated trips never mix
  - each ablation variant sees only its groups
  - real-data "not enough" paths
  - the admin card
- `python -m agripulse_ml.ablation` (~8 min) reproduces docs/ablation-results.md.
  `python -m agripulse_ml.ablation --provenance real` prints the readiness-based "not enough real data" report.
- Admin page → "V2 results": six studies, each with a SYNTHETIC or REAL badge.

## Pre-V3 B-1: forecast range calibration

> SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT for every study number below.

- **What changed:** a model-agnostic calibration wrapper, `agripulse_ml.calibration`, configured in
  `config/calibration.toml`.
  - It widens or narrows each horizon's p10–p90 range using the model's own track record.
  - The track record is the last 365 days of forecasts whose outcomes are already published.
  - The p50 is never moved, and the model itself is unchanged.
  - The displayed model is still `lightgbm_quantile` (rule 19).
- **Results** ([docs/calibration-results.md](docs/calibration-results.md)): same 8 datasets and final 8 folds as
  V2-0.
  - LightGBM mean coverage went from 79.4 / 78.4 / 76.8 / 74.2% to 80.8 / 79.7 / 79.1 / 78.6% at 1–4 weeks
    (target 80, tolerance ±5).
  - **The per-dataset spread is not fixed:** 17 of 32 dataset-horizons are within ±5 afterwards, against 15
    before.
  - Pinball loss gets 0.6–2.9% worse. Calibration makes the uncertainty labels honest; it does not improve
    accuracy.
- **Product:**
  - Forecasts store `p10_raw` / `p90_raw` alongside the displayed `p10` / `p90`, plus `calibration`
    (`applied` / `not_yet_applicable` / `none`). Migration 0008 adds these.
  - The chart shows a "Range calibrated / not yet calibrated" badge.
  - The baseline says "no calibration needed".
  - Real data has too short a track record, so real forecasts show **not yet calibrated**.
- **Harness:** every eval run reports `coverage_gap_pct` / `coverage_within_tol`.
  `eval.coverage.assert_coverage` raises `CoverageDrift` beyond the configured tolerance.

**How to verify**
- `pytest tests/test_calibration.py`: 9 tests.
  - Over-narrow ranges are calibrated to 80% on a controlled stream, by both methods.
  - The leakage tamper test passes: future outcomes don't change today's range.
  - A short record gives `not_yet_applicable`.
  - The wrapper works on naive and LightGBM, and the drift check fires.
  - The committed study keeps its mean coverage within tolerance.
  - Live offsets come from stored forecasts, and the API labels match.
  - The Admin card rows are present.
- `python -m agripulse_ml.calibration_study` (~15 min) regenerates `docs/results/calibration-*.csv`.
- `alembic upgrade head`, then `python -m agripulse_ml.predict`.
  - The job output shows a `calibration` status per horizon.
  - The Farmer page chart shows the calibration badge next to the provenance badge.
- Admin → "V2 results" lists the B-1 study (SYNTHETIC) and the live calibration status of the displayed forecasts.

## Pre-V3 B-2: driver app keeps tracking with the screen locked

The investigation is in [docs/driver-app-investigation.md](docs/driver-app-investigation.md). The chosen fix,
option (b), is in [docs/driver-android.md](docs/driver-android.md).

- **Android app** (`apps/driver-android`): a Capacitor 7 wrapper around the same `apps/driver-pwa` files.
  - Location comes from a native foreground service with an ongoing "AgriPulse: tracking on" notification.
  - It continues with the screen locked, during calls and with Maps in front.
  - It runs only between Start trip and End trip. It also stops on logout, withdrawn consent, or when the server
    refuses points.
  - The APK is built by `.github/workflows/driver-android.yml` (debug build; set the `AGRIPULSE_API` Actions
    variable first).
- **Browser app fixes:**
  - The screen lock is re-requested after every interruption. Before, one phone call let the screen sleep for the
    rest of the trip.
  - When hidden, the app reports "paused, screen off" to the server. On return it tells the driver how long
    location was paused.
  - A before-you-drive checklist on the trip screen.
  - One fix per 5 s.
  - Fixed: the red TRACKING ON bar stayed visible after a trip ended, because a CSS rule overrode the `hidden`
    attribute.
- **Server:**
  - `POST /trips/{id}/pause` sets `trips.tracking_paused_at` / `tracking_pause_reason` (migration 0009). The next
    fix clears it with a `tracking_resumed` event.
  - When a paused phone goes quiet, the monitor records the stop as `phone_paused`, and the fleet owner and FPO
    get a "Tracking paused — the vehicle may still be moving" alert instead of "Vehicle stopped".
  - The farmer's live view shows "Location paused since HH:MM".
  - CORS allows `https://localhost` (the Android app's origin).

**How to verify**
- `pytest tests/test_tracking_pause.py tests/test_driver_app.py`: 12 tests. `test_driver_app.py` runs the real
  app files in headless Chromium and skips without Playwright (`pip install -e .[browser]`). It checks:
  - pause needs an active, consented trip and the trip's own driver
  - an offline replay from before the pause doesn't resume it
  - the monitor and alert say "paused" rather than "stopped"
  - end and withdrawn consent clear the pause
  - Kannada copy exists, and CORS allows the app
  - the screen lock is re-acquired 3 times out of 3
  - the pause report is sent once, and the driver notice appears
  - one fix per 5 s
  - Android path: the plugin watcher shows a notification naming the trip, no screen lock is used, no pause is
    reported when hidden, and points upload over HTTP
  - GPS stops on logout and on a 409 from the server
- `alembic upgrade head` applies migration 0009.
- GitHub: Actions → driver-android shows a green run with an `agripulse-driver-debug` artifact.
- **Real phone** (field test): lock the screen for 10 min and use Maps for 10 min mid-route. Fixes keep arriving,
  and the notification is visible the whole time.

## V3-0: optimizer (OR-Tools) vs the V1 rule

> SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT for every study number below.

- **What it is:** `agripulse_api.decisions`, one database-free core for the API, the Admin comparison and the
  study.
  - `rule.py`: V1's lot-by-lot ranking, plus a nearest-truck dispatcher.
  - `optimizer.py`: CP-SAT over all lots, mandis and trucks at once.
    - Hard limits: truck capacity, spoilage ≤ 8%, and tonnes into a mandi ≤ 25% of its typical daily arrivals
      (minus trucks already on the way).
    - Maximises calibrated p50 net value (or p10, risk-averse).
  - `evaluate.py`: scores any plan with one cost model.
- **Results** ([docs/optimizer-results.md](docs/optimizer-results.md)): 8 synthetic datasets × 10 days × 3 batch
  sizes, scored at the price **realised** a week later.
  - The pre-registered switch rule was met, so **the optimizer is now the default** (`[recommender] default` in
    `config/recommender.toml`; `"rule"` brings V1 back).
  - Net value is a tie (+0.07%); violations fell from 140 to 0.
  - Transport cost is 22–34% lower. The dependable gain is truck assignment.
  - In dense batches it earns 1.3% less, because it refuses to flood mandis and flooding isn't priced in the scoring.
  - **Real data: not evaluable yet** (no real lots with a sale outcome).
- **Product:**
  - The farmer's best-mandi table lists options that break a limit last, with the reason.
  - Admin → "Recommenders: V1 rule vs optimizer" runs both on a simulated batch against today's forecasts.
  - New dependency: `ortools` (in `pyproject.toml`, so the Docker image picks it up).

**How to verify**
- `pytest tests/test_decisions.py`: 12 tests.
  - Every hard limit holds, and the rule is caught breaking them.
  - With limits off, the optimizer is never worse than the rule on its own objective (optimality).
  - Plans are graded at the prices given, not the forecast; the p10 objective works.
  - The farmer endpoint follows the config, and the rule stays callable.
  - Trucks already on the road reduce a mandi's room.
  - The Admin comparison works and is admin-only.
  - **The config default equals the pre-registered verdict on the committed CSV.**
- `python -m agripulse_ml.decision_study` (~40 min) regenerates `docs/results/optimizer-synthetic.csv`.
- Admin page → "Recommenders" → pick "Dense" → Compare. The rule shows mandi-room violations; the optimizer shows 0,
  with lower transport cost.

## V3-1: shared truckloads and return loads

> SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT for every study number below.

- **Shared loads** (`agripulse_api.decisions.loads`): up to 4 lots within 20 km share a truck, in the best pickup
  order (every order is checked).
  - Each lot's spoilage runs from its own pickup; there are 20 minutes of loading per extra stop and a 12-hour
    driver day.
  - Lots travelling alone keep all of V3-0's options, so sharing is only chosen when it plans better.
- **Return loads** (`decisions.returns`): after its first delivery, a truck takes one waiting lot on the way home
  instead of driving back empty.
- **Solver:** now stops on a deterministic work limit with 1 worker, so a batch gets the same plan on any machine at
  any load. V3-0 was re-run with this setting and its verdict is unchanged.
- **Results** ([docs/optimizer-results.md](docs/optimizer-results.md)): versus V3-0 on the same 240 batches, better
  on 204, worse on 2, with 0 violations.
  - +2% to +13% in sparse and medium batches.
  - Dense is +38% to +47%, but mostly from shipping twice the lots, with unshipped lots scored at ₹0. The honest
    per-tonne effect is 11–18% lower transport cost and 1–3% more value.
  - The pre-registered switch was met, so both features are **on**.
- **Product:**
  - FPO → "Plan shared truckloads". Accept creates one shipment per load.
  - Fleet owner → "Return loads" for trucks that delivered today. Accept assigns the same truck and driver.
  - Proposals are stored in `load_proposals` (migration 0011), audited, tenant-scoped, and marked stale if lots
    changed.

**How to verify**
- `pytest tests/test_loads.py`: 12 tests.
  - The pickup order is exact.
  - Sharing is never worse than one lot per truck on its own objective.
  - Per-lot spoilage clocks are correct, and a zero radius means no sharing.
  - Return loads only take waiting lots, one per truck, within the day and the mandi room.
  - FPO plan → accept creates the shipments; it can't be accepted twice, and a changed lot makes it stale.
  - Tenancy and reject work.
  - Fleet return load → accept creates the trip.
  - **The switches equal the pre-registered verdict on the committed CSV.**
- `python -m agripulse_ml.consolidation_study` (~50 min with cached forecasts) regenerates
  `docs/results/consolidation-synthetic.csv`.
- `alembic upgrade head` (0011).
- By hand:
  1. As the FPO, register a few lots near each other (farmer → new lot, choose the FPO).
  2. Go to FPO → "Plan loads". The shared loads and the saving are shown.
  3. Accept. The shipments appear.

## V3-2: scenario simulator

> **COUNTERFACTUAL ESTIMATE — not a validated causal model**, and SYNTHETIC for every result today.

- **Scenarios:** rainfall failure (districts, rain lost, window) and a tomato export ban (start date, optional
  regional share). Each is answered by **two separate channels**:
  - **(B) Documented assumption chain.** Supply shock → price through a sourced elasticity: −0.721, from RBI WP
    08/2024. Rain → yield uses FAO's ky of 1.05. Exports come from WITS (96.8 kt), against production from PIB
    (208.19 lakh t). Results have low / central / high ranges. Two links are marked **UNSOURCED** with wide ranges.
  - **(A) What the current model does.** Its rain and arrivals inputs are perturbed and it is re-run. This is a
    sensitivity of a synthetic-trained model.
- **Finding** ([docs/scenario-assumptions.md](docs/scenario-assumptions.md)):
  - (B) A 50% Jun–Jul deficit gives about +10% (range +0.6% to +49%). An export ban gives −0.3%, because exports are
    0.47% of output.
  - (A) gives wrong-sign or erratic answers to both. The UI flags when the two channels disagree.
- **Runs** are stored in `scenario_runs` (migration 0012). The `forecasts` table is never written.
- **Policy** → "Scenario simulator": a map coloured by p50 shift, a baseline-vs-scenario table, and the
  assumptions with their sources. The label appears on every output.
- API: `GET /scenarios`, `POST /scenarios/run`, `GET /scenarios/runs[/{id}]` (Policy and Admin).

**How to verify**
- `pytest tests/test_scenarios.py`: 14 tests.
  - The rule-22 label text is exact.
  - No shock means no shift. Directions and ranges hold, and the band never narrows (p90/p10).
  - Sources are recorded, and UNSOURCED is stated.
  - The harvest-lag window is applied.
  - Only the chosen districts move, and **the `forecasts` table is unchanged byte for byte**.
  - Without a trained model, channel A says "not available".
  - An export ban comes out at about −0.3%, and a user-set share is recorded.
  - Bad parameters get 422; access is Policy and Admin only.
  - With a trained model, a zero perturbation leaves the forecast identical, and a window outside the model's view
    is flagged.
- `alembic upgrade head` (0012).
- Policy page → Scenario simulator → rainfall failure, Kolar + Chikkaballapur, 50%, a window about 2–4 months ago
  → Run. Channel B shows about +10% in those mandis.

## V3-3: languages, dashboard polish, per-module status, backlog closure

- **Languages.** English, Kannada and **Hindi** for every alert template and all 83 UI strings (web: Shell language
  picker and register; alerts follow `users.preferred_lang`).
  - **Not native-reviewed yet.** Kannada and Hindi are machine drafts; every string carries a `_review` status and the
    Admin "Translations" card counts reviewed vs machine (today 0 reviewed).
  - Review workflow (docs/alerts.md): `python -m agripulse_api.i18n_tools export --lang kn --out kn.csv` → the
    reviewer fills `corrected` and/or `approve` → `python -m agripulse_api.i18n_tools import --lang kn kn.csv
    --reviewer "<name>"`. Placeholders must match or the whole import is refused.
- **Per-module data status (rule 23).** Admin and Policy show one row per module: price forecast, mandi graph,
  satellite, in-transit feature, optimizer (shared + return loads), scenario simulator. Each is `real`,
  `real_partial`, `synthetic` or `not_yet_evaluable`, computed live from readiness and stored provenance, with
  per-mandi counts where they apply. `GET /module-status` (Policy and Admin).
- **Dashboard polish** (all 9 roles, desktop; farmer, driver and trader at 390 px), checked with a headless-browser
  audit for horizontal overflow, API errors and SYNTHETIC labels. Fixed:
  - the mobile header overflowed by 27 px → sign-out moved to the new **Account** page (devices list, sign out one
    device or everywhere);
  - the farmer's price table hid the price column on a phone → columns reordered (mandi, price, range);
  - the trader's "expected vs normal" showed "0%, well below normal" when nothing was tracked → now "–, no tracked or
    weighed arrivals yet — not a supply signal".
  Every page that shows a forecast or price number carries `ProvenanceBadge`; pages without model numbers (FPO,
  driver, fleet, lender) have none to label.
- **Backlog closed** (details and the deferred list with reasons in [docs/backlog.md](docs/backlog.md)):
  - #3 mandi location tool: Admin → Mandi locations, OSM candidates from Nominatim, a person confirms; moves are
    audited and reset `verified`. **The 18 mandis still need confirming by a person in a deployment.**
  - #7 map tiles: self-hosted PMTiles, a style URL or raster tiles ([docs/map-tiles.md](docs/map-tiles.md)); Admin
    warns while the public OSM default is in use.
  - #4 end-trip guard (+ fleet-owner close with a reason), #6 WebSocket tickets, #11 pinned synthetic draw, #17 no
    delay alerts from a paused phone, #19 device list, #21 session clean-up; #1, #5 and #18 closed as superseded.
  - Fixed in passing: prod CORS had dropped `https://localhost`, which the Android app needs.

**How to verify**
- `pytest tests/test_i18n.py tests/test_mandi_locations.py tests/test_module_status.py tests/test_backlog_v33.py`:
  17 tests. The i18n test fails on any missing kn/hi string or placeholder mismatch; the import is all-or-nothing.
- `python -m agripulse_api.i18n_tools status`: counts per file and language.
- Switch the language to हिन्दी in the header: the screens and new alerts are in Hindi.
- Admin → Module status: with the demo data every price-dependent module says `synthetic`; satellite follows the
  stored observations.
- Admin → Mandi locations → pick a mandi → OSM candidates appear (needs `NOMINATIM_CONTACT` in .env and outbound
  access to nominatim.openstreetmap.org) → "Use" one or click the map → "Confirm location" → the mandi shows
  verified; saving a different point again clears that.
- A driver pressing "End trip" before the delivery scan is refused; a fleet owner closes such a trip with
  `POST /trips/{id}/close {"reason": ...}` (API only: there is no Fleet-screen button yet).
- Account page lists your devices; signing one out ends its WebSocket within the recheck interval.

## V3-4: real backtest and evaluation package

- **Real spike backtest** (`python -m agripulse_ml.real_backtest`, [docs/backtest-real.md](docs/backtest-real.md)).
  - Event-level rules were pre-registered: lead time, recall, false-alarm days, a fixed and a learned threshold, and a
    minimum of 5 events.
  - **Real result: not enough real data.** There are 17 real price rows (one day). A first fold is possible from
    2027-10-24, or as soon as older Agmarknet history is backfilled (`ingest.run backfill`).
  - The same rules on 8 synthetic draws (SYNTHETIC, never blended): LightGBM at 0.5 catches 40% of spikes, a median
    of 12 days ahead; naive cannot warn; the learned threshold drifts to near always-on (backlog 31).
  - Admin/Policy module status has a live "Real spike backtest" row.
- **Evaluation package:**
  - [docs/final-evaluation.md](docs/final-evaluation.md): real vs synthetic vs not yet provable, in one place (rule 24).
  - [docs/paper.md](docs/paper.md): paper-style write-up.
  - [docs/pitch-outline.md](docs/pitch-outline.md): pitch outline (also built as a slide deck).
- **Demo videos:** `scripts/record_demo.py` records two captioned clips from a running demo stack:
  - `tejas.mp4`: the journey from lot to delivery. The truck is SIMULATED, and the real driver app is clicked through.
  - `decisions.mp4`: the optimizer, shared loads and the scenario simulator.
  The real-phone video from the field test is still to be recorded. The videos are not committed.

**How to verify**
- `pytest tests/test_real_backtest.py`: 7 tests.
  - Event and rise-date detection; lead time and false-alarm days.
  - The 5-event gate withholds rates, and an always-on alarm is exposed.
  - The learned threshold is unchanged when future labels are tampered with.
  - The real path ignores synthetic rows, and runs end to end on planted real-source history.
- `python -m agripulse_ml.real_backtest` on your database prints `not_enough_real_data` and the earliest fold date
  until a year of real prices exists.
- `DATABASE_URL=... python scripts/record_demo.py --out demo-videos` with the demo stack running (a fresh demo
  database) writes both MP4s.

## Farmer booking, live tracking and payment (after V3-4)

- **The farmer's path after "Sell here" (lot page → Next steps):**
  1. Choose a transporter: trucks that fit, estimated fare, open pickup slots.
  2. Pick a time and **Book**. This creates a shipment booked with that fleet, and the fleet owner gets an alert.
  3. The fleet owner **confirms** by assigning a truck and driver, or **declines** (the lot goes back to registered).
     The farmer is alerted either way.
  4. The driver runs the trip, and the farmer follows it on the **Live vehicle** map.
  5. At the mandi, the trader scans the delivery QR, weighs the lot and **records the payment** (UPI, bank, cash; a
     reference is optional). The farmer taps **I received it**.
- **Payments are recorded, not processed:** no money moves through AgriPulse.
- **Other routes still work:** the farmer can ask their FPO to arrange the shipment instead, and the FPO flow is
  unchanged.
- **Public demo only (`DEMO_MODE=true`):** **Run demo trip (simulated driver)** lets a SIMULATED transporter, driver
  and trader run the booked trip in about two minutes. The truck, trip and payment are all labelled simulated.
  Real deployments return 404 for this.
- **API:**
  - `GET /lots/{id}/transport-slots`
  - `POST /lots/{id}/bookings`
  - `GET /bookings`
  - `POST /bookings/{id}/cancel`, `POST /bookings/{id}/decline`
  - `POST /trader/lots/{id}/payment`
  - `POST /lots/{id}/payment-received`
  - `POST /lots/{id}/demo-trip`
- **Migrations:** 0013–0015.

**How to verify:** `pytest tests/test_bookings.py tests/test_preferred_mandi.py`.

- **Delivery receipt (proof for the mandi):** once the lot is weighed, the lot page shows "Open delivery receipt":
  receipt number, parties, weight, rate, amount, payment status and the evidence timeline (QR scans, geofence
  events, GPS count), with Print / Save as PDF and a verification QR/link. Demo receipts are watermarked SIMULATED.
- **Live tracking on both legs:** the map shows the truck coming to the farm (reached_pickup), then moving from the
  farm to the mandi after the pickup scan, with a progress bar for each leg.
- **Vegetable choice:** 11 vegetables (config/crops.toml). Only tomato has a price forecast; others are ranked by
  transport cost and crop spoilage sensitivity, with no price shown.

How to verify: farmer login → new lot, pick Onion → Sell here → Choose time → Book → Run demo trip → watch both
legs → Open delivery receipt → Print.

## Security

- **Passwords:** PBKDF2-SHA256 with 240k iterations and a per-user salt (`agripulse_api/security.py`).
  `JWT_SECRET` and `ADMIN_PASSWORD` live only in `.env`.
- **Tokens:**
  - Access tokens are JWTs that last 30 minutes (`JWT_EXPIRE_MINUTES`). Refresh tokens last 14 days
    (`JWT_REFRESH_DAYS`).
  - Both carry a **session id**: each sign-in is one row in `user_sessions` (Pre-V3 B-3, migration 0010).
- **Every request checks the session** (HTTP and WebSocket). A revoked session fails on its **next request**, not
  when its access token runs out.
  - The driver's GPS socket checks on every message.
  - Live map and alert sockets re-check every 60 s and close with code 4401.
- **Refresh tokens rotate:** each refresh retires the old one.
  - A retired refresh token presented again is treated as theft: the session is revoked and the event audited
    (`via: reuse_detection`).
  - The one exception is the same token within 30 s, so two open tabs refreshing together don't sign the user out.
- **Revocation:**
  - **Admin → Users → Sessions → "Revoke all"** (`POST /admin/users/{id}/revoke-sessions`, optional reason). Signs
    the user out everywhere now. They can sign in again unless you also untick **Active** (disable). History is at
    `GET /admin/users/{id}/session-audit`.
  - **"Sign out everywhere"** (web header) or **"All devices"** (driver app) → `POST /auth/logout-all`. For a lost
    phone or a password someone else may know.
  - **"Sign out" / "Log out"** → `POST /auth/logout` ends this device's session on the server, so a copied token
    stops working too.
  - Every revocation writes `audit_log` (entity `user`, field `sessions`) with who (`actor_id`; empty = automatic
    reuse detection), when, why, how many sessions, and `via` (admin / self_service / logout / reuse_detection).
- **Upgrading to B-3:** tokens issued before migration 0010 carry no session id and are refused, so **everyone
  signs in once**. Upgrade when no trip is in progress: a driver mid-trip would have to sign in again.
- **Tenancy:** cross-tenant reads return 404, not 403 (`agripulse_api.scoping`). Public tracking links are
  expiring, unguessable tokens exposing only position, ETA and lot status (rule 4).

**How to verify (B-3)**
- `pytest tests/test_sessions.py`: 9 tests.
  - An admin revoke fails both of the user's devices on the next request and on refresh.
  - Other users are unaffected.
  - The audit row is correct (actor, reason, count, time).
  - A non-admin gets 403.
  - Self-service "all devices" works, and single-device logout leaves the other device signed in.
  - Refresh rotation works, the 30-second grace works, and reuse revokes the session.
  - Tokens from before sessions are refused, and disabled users stay blocked.
  - A revoked driver's GPS socket closes (4401) and HTTP points get 401.
  - Live viewers are disconnected.
- `pytest tests/test_driver_app.py -k log_out`: the driver app's "Log out" ends the session on the server.
- By hand:
  1. Sign in as a farmer in one browser.
  2. In another, as admin, go to Users → Farmer → "Revoke all", give a reason and confirm.
  3. The farmer's next page load goes to the sign-in page. The Sessions column drops to 0.

## V1 status

| Done-criterion | Status |
|---|---|
| Prices and weather update daily on their own; Admin shows freshness per source | **Built** (worker schedules + freshness panel). Needs your `DATA_GOV_API_KEY` and a running worker to go green. |
| Tomato forecast gives 1–4 week ranges with a documented backtest vs baseline | **Built, backtested on synthetic data only.** Re-run `train` once there is more than ~13 months of real history. |
| A real phone drives a real route while a farmer watches live | **Built and exercised with an emulated phone in a real browser.** Real-road run pending: [field-test.md](docs/field-test.md). |
| All 9 roles log in and complete their core task | **Done**: API tests + browser journey. |
| 3-minute demo of Tejas's journey | **Script written** ([field-test.md](docs/field-test.md#3-minute-demo-video-script-tejass-journey)); recording is yours. |

### Stubbed / known limitations

- **No real data in the repo.** The sandbox this was built in couldn't reach data.gov.in / Open-Meteo / NASA POWER.
  Clients follow documented and observed response shapes, and the Agmarknet fixture is a real payload, but run each job
  once with real access and check `docs/data-sources.md`.
- **Agmarknet arrivals (tonnes)** aren't in the daily API; they come from bulk CSV backfill and trader-confirmed weighings.
- **Kannada** strings (alerts and UI) are machine-drafted and marked unreviewed.
- **Mandi coordinates** are approximate town centroids (`coords_verified = false`); verify before relying on geofences.
- **Festival flags** are approximate calendar windows, not the lunar dates.
- **Multi-pickup trips** use one tonnage-weighted pickup point; multi-stop routing is V3 (OR-Tools).
- **Refresh tokens** aren't individually revocable; disabling a user blocks refresh.
- **Map tiles** come from the public OSM server (demo use only); see deployment notes.
- **Not built (V2/V3 by design):** TFT, mandi GNN / Neo4j, Sentinel-2, in-transit supply as a model feature, OR-Tools,
  scenario simulator, Hindi.

## Real prices for every vegetable (LIVE mode)

- **Real mandi prices:** the daily Agmarknet job pulls every commodity for Karnataka and neighbouring states and keeps
  every vegetable a farmer can pick. The farmer page shows nearby mandi prices for the chosen vegetable (with the
  date each mandi reported), and "Best mandi" ranks by the value of the lot at the latest real price, after transport
  and spoilage. That is today's price, not a forecast, and the page says so.
- **Add a vegetable:** "+ Add another vegetable…" in the Vegetable list; names the live feed reports are suggested. A
  matched name gets real prices from then on; an unmatched one still works for booking, tracking, receipt and payment.
- **Tomato forecast on real data:** trained only on real rows, and only after the readiness monitor finds a mandi with
  a year of real history (history backfill from data.gov.in, probed first). Until then there is no forecast.
- **Still simulated:** demo accounts and demo trucks. A simulated weighing prices the lot at that vegetable's latest
  real price at that mandi (else the tomato forecast, else a stated assumption); the receipt shows which (`rate:`).
- **The banner reads `/data-status`**, so it always says whether prices are real, missing (no key) or synthetic.
- Setup: docs/deployment.md "LIVE mode". Tests: tests/test_live_prices.py.

How to verify: set `DATABASE_URL` + `DATA_GOV_API_KEY`, deploy, then check the API log for `[live] agmarknet` and
`history probe findings`; farmer login → pick Onion → nearby prices show today's Agmarknet rows.

## More transporters (demo)

Eight demo transport companies based in Hebbal, Kolar, Chintamani, Mulbagal, Chikkaballapur, Hosakote, Tumakuru and
Mysuru, each with its own drivers and trucks (2.5-16 t, `KA-DEMO-…`, simulated). The farmer sees those within 120 km of
the farm, cheapest first; the fare counts the whole truck day (base -> farm -> mandi -> base). "Run demo trip" uses the
chosen company's own truck and driver, starting from its base. Seeded by `seed.seed_demo_fleets`; base columns in
migration 0018.

## Booking confirmation and pickup code

After the farmer books, the transporter confirms and assigns a driver and truck (demo: within a few seconds). The farmer
sees the driver's name and the truck, follows it to the farm on the live map, and at the farm enters the 4-digit
pickup code the driver tells them (`POST /lots/{id}/confirm-pickup`; 5 wrong tries lock it, the QR scan still works).
Only then does the truck leave for the mandi. The driver app shows the code. In the public demo there is no real
driver, so the demo driver's code is shown under the input.

How to verify: farmer → lot → Sell here → Choose time → Book → wait for "Confirmed" → when the truck reaches the farm,
enter the code shown → the truck heads to the mandi.

## FPO members

The FPO desk can add member farmers (name, phone) and register harvest lots on their behalf ("+ Add lot for a
member": member, vegetable, quantity, grade, village, pickup point on the map). Many members don't use apps; the FPO
acts for them, and every lot it registers is audited with the FPO user as the actor. Endpoints: `GET/POST /fpo/members`,
`POST /fpo/lots` (permission `members:manage`). The public demo seeds six members of the Kolar FPO with lots waiting
(`seed.seed_demo_members`, only from `python -m agripulse_api.seed --demo`, not the test fixture).

How to verify: FPO login → Members' lots shows six waiting lots → "+ Add member" → "+ Add lot for a member" → tick lots →
create a shipment, or "Plan loads".

## Sign-up rules per role

- Farmer, bulk buyer: anyone can sign up.
- Mandi manager / trader: chooses the district, then their mandi in that district.
- Driver: only phone numbers a fleet owner added (Fleet page → Drivers → Add driver) can sign up; on that first sign-up
  the driver enters the vehicle number (added to the fleet if new) and the district they join.
- Fleet owner, FPO, lender / insurer, policy analyst: organisation name + district (policy may choose state-wide). A fleet
  owner's district becomes the base its trucks start from.
- Public lookup: `GET /auth/districts`. FPO bookings now get the same confirm → driver + truck → pickup-code flow as
  farmers (`POST /shipments/{id}/confirm-pickup`).

## Real accounts alongside the demo

Accounts are stored in the database (Neon), so they persist. A real farmer's lot reaches real people: a real transport
company confirms its own bookings (Fleet page), and at a mandi with a real manager the lot waits under "Awaiting
weighing" for that manager to weigh it and record the payment. The demo companies and the demo mandi manager only act
for bookings made with demo companies / at mandis without a real manager. Sign-ins survive restarts (signing key kept in
`app_settings`).

How to verify: register a farmer and a mandi manager for Kolar APMC → farmer books a demo transporter to Kolar APMC →
enter the pickup code → when the truck reaches the gate the manager weighs and records payment → the farmer sees it.

## Direct farmer → driver booking (real-time)

A third way to book, next to FPO → fleet → driver and farmer → transport company. A driver goes **online** for a
district and the mandis they deliver to, with their own (real) truck. A farmer presses **Request a driver near you**;
every matching online driver gets the request **at once**: over the existing `/ws/live` WebSocket (under a second while
the app is open) and as a **Web Push** notification (reaches a locked Android phone, usually in 1–5 s). The first
driver to accept gets the trip, which then follows the normal lifecycle from "accepted" (pickup code / QR, consent,
GPS, geofences, delivery QR, weighing, payment). No matching driver → the farmer sees "No drivers are currently
available for <mandi>" immediately; no answer in 5 minutes → expired; everyone declines → declined.

Only real accounts and real trucks take part, so every direct trip is `is_simulated = false`; Admin → **Trips by
booking channel** separates direct, company, FPO and demo-autopilot trips and shows how fast drivers saw each request.
Web Push keys are generated once and kept in the database (or set `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY`).

How to verify: `pytest tests/test_direct_booking.py` (two real accounts: zero drivers, wrong mandi / district, stale
phone vs push-enabled phone, live WebSocket delivery, push hand-off, first-accept-wins, decline, expiry, cancel, demo
accounts and sample trucks refused, then pickup code + GPS on the same trip). On two phones: `docs/field-test.md`
→ "Two phones: direct farmer → driver booking".

Stubbed / known limits: push delivery time is up to Google/Apple (battery saver can delay it); the Android (Capacitor)
app gets requests over the socket only (no push inside the WebView); requests expire lazily (on the next read).
