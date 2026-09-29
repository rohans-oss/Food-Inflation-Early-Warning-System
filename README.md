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

**Honest result so far (synthetic data only):** LightGBM beats naive pinball by 1–2% at 1 week (a near-tie), rising
to 8–12% at 3–4 weeks. Intervals are slightly too narrow (mostly 74–77% coverage vs the 80% target). Spike recall at a 0.5 threshold is poor. None of
this says anything about real tomato prices until it's re-run on Agmarknet history.

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
