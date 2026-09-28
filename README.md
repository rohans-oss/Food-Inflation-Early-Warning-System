# AgriPulse: Food-Inflation Early-Warning System

Forecasts tomato prices 1–4 weeks ahead as **ranges** (p10 / p50 / p90 plus a spike probability) and tracks produce vehicles
live from farm to mandi, so supply in motion becomes a leading price signal.

**Status: Version 1 (Live Platform), code complete.** Scope is tomato, Karnataka and neighbouring states.
Two "done" items still need people and time rather than code: a real-phone field test and the demo video
(`docs/field-test.md`), and a backtest on real history once the Agmarknet backfill is in (`docs/backtest.md`).

```
Agmarknet (daily) · Open-Meteo (hourly) · NASA POWER (daily) ──> ingest jobs (APScheduler) ──> PostgreSQL/PostGIS + TimescaleDB
Driver PWA ──WebSocket──> FastAPI tracking ──> Redis pub/sub ──> live maps          │
                                  │                                                 │
          FastAPI: auth/RBAC, lots, shipments, trips, prices, forecasts, recommender, alerts
                                  │
          Next.js + MapLibre: 9 role dashboards · public tracking link · driver PWA
ML: features → LightGBM quantile (+ naive / seasonal-naive baselines) → walk-forward backtest → forecasts table (MLflow optional)
```

## Repository layout

| Path | What |
|---|---|
| `apps/web` | Next.js (TypeScript, Tailwind, MapLibre) dashboards for all 9 roles, plus the public tracking page `/track/<token>` |
| `apps/driver-pwa` | Driver PWA (served by the API at `/driver/`): trip list, accept/decline, consent, GPS with IndexedDB offline buffer, QR scan |
| `services/api` | FastAPI app, SQLAlchemy models, Alembic migrations, RBAC and tenancy, alerts |
| `services/ingest` | Agmarknet / Open-Meteo / NASA POWER jobs, cleaning layer, scheduler |
| `services/tracking` | GPS ingest, geofences, ETA, OSRM client, live hub, trip simulator |
| `ml` | features, models, walk-forward evaluation, training, prediction |
| `infra` | API Dockerfile, OSRM preparation |
| `docs` | data sources, forecasting, backtest, routing, alerts, field test |

## The 9 roles (all log in, all have a working screen)

| Role | Screen | Core task |
|---|---|---|
| Farmer | `/farmer` | Register a lot; nearby prices; 1–4 week range; best-mandi ranking; live vehicle + ETA; pickup QR; delivery confirmation; share with a lender |
| FPO / aggregator | `/fpo` | Group lots into a shipment, book a fleet, farmer-wise tonnage, mark payouts |
| Driver | PWA at `<api>/driver/` | Accept trip, consent, start/end, background GPS with offline buffer, scan pickup QR, show delivery QR |
| Fleet owner | `/fleet` | Add vehicles, approve drivers, assign bookings, all vehicles on one live map, trip history, 30-day utilisation |
| Mandi trader | `/trader` | Incoming-supply board (trucks, tons, ETA), expected-today vs normal, scan delivery QR, record weight and price |
| Bulk buyer | `/buyer` | Forecasts and expected tons at watched mandis, spike alerts |
| Policy analyst | `/policy` | State/district map: price trend, spike probability, tons in transit, arrival anomalies |
| Lender / insurer | `/lender` | Verified chain per lot (pickup → route → delivery → weight) and a trip-reliability score, for lots farmers chose to share |
| Admin / data ops | `/admin` | Freshness per source, failed-job log, data quality + mandi coordinates, model performance, users, simulator |

Auth is JWT. One permission map (`services/api/agripulse_api/rbac.py`) covers every role. FPOs, fleets, buyers, lenders
and government are **tenants**: queries are filtered by organisation (`scoping.py`), and the tests check that one FPO
can't read another's lots.

## Quick start

### Docker (everything)

```bash
cp .env.example .env            # set JWT_SECRET, ADMIN_PASSWORD; DATA_GOV_API_KEY for live prices
docker compose up -d --build    # db (TimescaleDB+PostGIS), redis, api :8000, worker, web :3000
docker compose exec api python -m ingest.run synthetic        # optional: labelled synthetic history for a demo
docker compose exec api python -m agripulse_ml.train --synthetic && docker compose exec api python -m agripulse_ml.predict
```

Open <http://localhost:3000>. The login page lists the demo accounts (password `DEMO_PASSWORD`, default `agripulse-demo`).
Routing: see `docs/routing.md` to add self-hosted OSRM (`docker compose --profile routing up -d osrm`).

### Local dev (no Docker)

```bash
pip install -e ".[dev]"
cp .env.example .env                       # JWT_SECRET at minimum; SQLite by default
alembic upgrade head
python -m agripulse_api.seed --demo
python -m ingest.run synthetic             # or: python -m ingest.run agmarknet (needs DATA_GOV_API_KEY)
python -m agripulse_ml.train --synthetic --folds 6 && python -m agripulse_ml.predict
uvicorn agripulse_api.main:app --reload --app-dir services/api      # :8000, driver app at /driver/
cd apps/web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # :3000
python -m ingest.scheduler                 # daily/hourly jobs, nightly forecast, weekly retrain
```

## Data freshness ("real time" per source)

| Source | Freshness | Key | Job (IST) |
|---|---|---|---|
| Agmarknet via data.gov.in | daily, current day only | yes (free) | 13:10 and 19:40 |
| Open-Meteo | hourly model runs | no | every hour at :05 |
| NASA POWER | daily, a few days' lag | no | 06:20 |
| Forecast + spike alerts | nightly | – | 20:30 |
| Model retrain (real data only) | weekly | – | Sunday 02:15 |
| Driver phone GPS | every 5–10 s during a trip | – | live |

Every run is written to `data_source_runs` and shown under Admin → Data freshness / Failed jobs. Start the worker on
day 1: the daily Agmarknet API serves only the current day, so your own pulls **are** your history. Details and the
exact resource ids are in `docs/data-sources.md`.

## Non-negotiables, and where they're enforced

| Rule | Where |
|---|---|
| Simulated = `is_simulated = true` + "Simulated" label | simulator, `SimBadge` on every vehicle / trip / lot view; synthetic prices and models carry "Synthetic data" |
| Walk-forward only | `ml/agripulse_ml/evaluate.py`; look-ahead test in `tests/test_ml.py` |
| Forecast = p10/p50/p90 + spike probability | `forecasts` table, `ForecastRanges` component; no screen shows a single number |
| Track only during an active trip, with consent and a visible indicator | server rejects points otherwise (`TrackingNotActive`); PWA "TRACKING ON" banner |
| Public link = expiring, unguessable token; position, ETA and lot status only | `/public/track/{token}`, `/ws/track/{token}`; key set pinned by tests |
| No invented endpoints | `docs/data-sources.md` records what was verified and what is still to check with your key |
| Secrets only in `.env` | `.env.example` |

## How to verify V1

1. `pytest`: 41 tests covering auth + 9 roles + tenancy, ingestion + cleaning, ML (no look-ahead, quantile order, walk-forward),
   the full Tejas journey through the API, geofences, delay / stop alerts, the public link and websockets, and the simulator.
2. `cd apps/web && npm run build && npm run typecheck`.
3. Follow Quick start, then log in as each demo role. Run Tejas's journey by hand (checklist in `docs/field-test.md`),
   or start the simulator from Admin → Simulator to watch labelled synthetic trucks move on the fleet, trader and policy maps.
4. Admin → Model performance shows the backtest. Read `docs/backtest.md` for what it does and doesn't prove.

## Docs

- `docs/data-sources.md`: endpoints, verified fields, cleaning rules, synthetic data policy
- `docs/forecasting.md`: features, models, spike definition, walk-forward protocol
- `docs/backtest.md`: backtest vs baseline (currently a synthetic pipeline check, with the steps for the real one)
- `docs/routing.md`: OSRM, ETA, geofences, live pipeline
- `docs/alerts.md`: alert rules, channels, English + Kannada
- `docs/field-test.md`: real-phone field test and demo-video script

## Next (V2)

TFT forecaster, mandi graph + GNN, Sentinel-2 crop signal, in-transit tonnage as a model feature, ablation study.
