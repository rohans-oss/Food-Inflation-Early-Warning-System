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

## V2-4: Sentinel-2 crop signal (in progress)

Built and tested on local fixtures (no satellite result yet; see [docs/satellite.md](docs/satellite.md)):

- Scene search on Earth Search, and cropland NDVI per district from 80 m COG overviews.
  - Per-scene reflectance offset, cloud masking with the scene classification layer, ESA WorldCover cropland.
  - Endpoints and fields checked against real responses (docs/data-sources.md).
- A resumable pilot runner with a dry-run cost estimate.
- `satellite_obs` table (migration 0007) and a loader.
- `satellite` feature group with a 2-day publication lag, leakage-tested against a planted leak.
- Within-district validation statistics that say "too few points" when n is small.

Waiting on: the imagery fetch on a machine that can reach the sources, and the ground-truth files. The ICRISAT
apportioned data ends in 2011 and has no tomato, so it can't validate Sentinel-2 (2015+).

**How to verify**
- `pytest tests/test_satellite.py`: 7 tests.
  - STAC paging and cloud filter
  - NDVI exactly 0.75 / 0.50 under the two offset conventions
  - cloud and non-cropland pixels dropped
  - resumable run and idempotent load
  - lag and truncation leakage
  - validation verdicts
- `python -m agripulse_ml.satellite.run --out data/satellite --dry-run` (needs network to Earth Search).

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
