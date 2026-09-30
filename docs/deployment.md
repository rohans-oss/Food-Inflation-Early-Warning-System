# Deployment

## Local development (no Docker)

```bash
pip install -e ".[dev]"
cp .env.example .env                  # set JWT_SECRET
alembic upgrade head
python -m agripulse_api.seed --demo   # roles, mandis, admin, one demo login per role
python -m ingest.run synthetic        # OPTIONAL: labelled synthetic history so forecasts exist
python -m agripulse_ml.train --synthetic && python -m agripulse_ml.predict
uvicorn agripulse_api.main:app --reload --app-dir services/api      # :8000, driver PWA at /driver/
cd apps/web && npm install && npm run dev                             # :3000
```

SQLite is fine for development. Postgres-only features (PostGIS columns, Timescale hypertable) are skipped on SQLite.

## Docker Compose (dev / LAN)

```bash
cp .env.example .env    # JWT_SECRET, ADMIN_PASSWORD, DATA_GOV_API_KEY
docker compose up -d --build
```

| Service | What | Port |
|---|---|---|
| db | TimescaleDB + PostGIS (`timescale/timescaledb-ha:pg16`) | 5432 |
| redis | live-update fan-out between API processes | 6379 |
| api | FastAPI, runs migrations + seed on start | 8000 |
| worker | APScheduler: Agmarknet 13:10 + 19:40, Open-Meteo hourly, NASA POWER 06:20, forecast + spike alerts 20:30, stale-trip monitor every minute (IST) | – |
| web | Next.js | 3000 |
| osrm | only with `--profile routing`, see `docs/routing.md` | 5000 |

The worker writes models to the `models` volume and raw Agmarknet payloads to `rawdata`. **Back up `rawdata`:** the
API only serves the current day, so those files are your only copy of the history.

## Production (HTTPS, one domain)

```bash
# DNS: A record for agripulse.example.org -> this server
DOMAIN=agripulse.example.org docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- Caddy terminates TLS (certificate issued automatically) and routes `/` → web, `/api/*` → API (prefix stripped,
  WebSockets included). The driver PWA is at `https://$DOMAIN/api/driver/`.
- HTTPS is **required**, not optional: phones only give GPS and camera to secure pages.
- The overlay closes the db / redis / api / web ports, turns demo users off, sets `PUBLIC_BASE_URL` and CORS.
- The API refuses to start on Postgres with the default `JWT_SECRET`, and the seed refuses to create an admin
  without `ADMIN_PASSWORD`.
- Caddy strips `token` / `share` query parameters from access logs (WebSocket auth rides in the query string).
- Map tiles come from `tile.openstreetmap.org`, which is fine for a demo but its usage policy doesn't allow heavy
  production traffic. For a pilot, run your own tile server or use a provider, and change `STYLE` in
  `apps/web/components/MapView.tsx`.

## Public demo (Render, $0)

A shareable demo with **SYNTHETIC** data only. It is not the real system: nothing is collected, and it resets on every
restart.

- **API:** a Render web service built from `infra/Dockerfile.demo` (free plan).
  - The image bakes a SQLite database: reference data, 8 demo logins (password `agripulse-demo`), labelled synthetic
    history, a synthetic-trained model, forecasts and the mandi graph.
  - `scripts/demo_start.py` starts it with `JWT_SECRET=auto`, a random secret per process, so a restart signs
    everyone out.
  - The admin account is **disabled** unless you set `ADMIN_PASSWORD` in the Render dashboard yourself.
  - Free instances sleep after about 15 minutes idle; the first request then takes about a minute, and all changes
    are lost.
- **Environment:** `PUBLIC_BASE_URL` = the web address, and `CORS_ORIGINS` = the web address plus
  `https://localhost` (for the Android app). The API refuses to start on an `https` `PUBLIC_BASE_URL` with the
  default JWT secret.
- **Web:** a second Render web service built from `apps/web/Dockerfile` (context `apps/web`). Set
  `NEXT_PUBLIC_API_URL` = the API's Render URL and `NEXT_PUBLIC_DEMO_NOTICE` = the banner text shown on every page;
  both are build arguments. Vercel also works (root `apps/web`, same two variables) once the Vercel GitHub app has
  access to the repository.
- **Driver app:** `https://<render-url>/driver/`.
- **Not included:** the worker/scheduler, Redis, OSRM (routes are approximate and say so) and self-hosted tiles
  (the public OSM tiles, fine for a demo).

## First run on real data

```bash
docker compose exec api python -m ingest.run agmarknet          # needs DATA_GOV_API_KEY
docker compose exec api python -m ingest.run nasa_power --days 1500
docker compose exec api python -m ingest.run backfill /app/data/history.csv --state Karnataka   # bulk history, if you have it
docker compose exec api python -m agripulse_ml.train             # refuses to run without real data (no --synthetic)
docker compose exec api python -m agripulse_ml.predict
```

The walk-forward backtest needs more than about 13 months of daily prices (365 days of training + 28 days of 4-week targets); until then `train` stops with a clear message.

## Health

`GET /health` → `{"status": "ok", "db": "ok", "version": "1.0.0"}`; compose uses it for the API healthcheck.
Data freshness per source is on the Admin page (`GET /admin/freshness`).
