# AgriPulse — Food-Inflation Early-Warning System

Forecasts tomato prices 1–4 weeks ahead as **ranges** (p10 / p50 / p90 + spike probability) and tracks produce vehicles
**live from farm to mandi**, so supply in motion becomes a leading price signal. Nine user roles, one platform.

**Status: Version 1 (Live Platform) — code complete.** Two V1 done-criteria need you in the real world:
the field test and the demo video. See [V1 status](#v1-status).

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
| `infra` | Dockerfile, Caddyfile, OSRM prep |
| `docs` | [data sources](docs/data-sources.md) · [forecasting](docs/forecasting.md) · [alerts](docs/alerts.md) · [routing](docs/routing.md) · [deployment](docs/deployment.md) · [field test + demo script](docs/field-test.md) |

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

Demo logins (after `--demo`): `tejas@`, `fpo@`, `driver@`, `fleet@`, `trader@`, `buyer@`, `policy@`, `lender@` `demo.agripulse`;
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
