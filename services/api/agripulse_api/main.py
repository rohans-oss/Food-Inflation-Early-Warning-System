"""AgriPulse API entrypoint: `uvicorn agripulse_api.main:app --reload`."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import get_db
from tracking.hub import hub

from .routers import (admin, auth, bookings, graph, lots, mandis, members, prices, proposals, receipts, roles, scenarios,
                      trips)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    public = settings.public_base_url.startswith("https://")
    if settings.jwt_secret in ("", "change-me-in-.env") and (public or not settings.database_url.startswith("sqlite")):
        raise RuntimeError("Set JWT_SECRET (or JWT_SECRET=auto for a one-instance demo) before running against a real "
                           "database or on a public https address")
    await hub.start()
    scheduler = None
    if settings.enable_scheduler:
        from ingest.scheduler import start_scheduler

        scheduler = start_scheduler()
    if settings.demo_mode and not settings.live_catch_up:  # public demo: trucks heading to the demo mandi
        if settings.demo_refresh:
            from ingest.demo_refresh import start as start_refresh

            start_refresh()  # then starts the traffic
        else:
            from .routers.bookings import start_demo_traffic

            start_demo_traffic()
    if settings.live_catch_up:  # LIVE mode on free hosting: do whatever the sleeping scheduler missed
        from ingest.live import start_catch_up

        start_catch_up()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    await hub.stop()


app = FastAPI(title="AgriPulse API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, admin, mandis, prices, lots, members, bookings, receipts, trips, roles, graph, proposals, scenarios):
    app.include_router(r.router)


# Driver PWA served same-origin with the API at /driver/ (works on a phone over one URL).
_pwa = Path(__file__).resolve().parents[3] / "apps" / "driver-pwa"
if _pwa.is_dir():
    app.mount("/driver", StaticFiles(directory=_pwa, html=True), name="driver-pwa")


@app.get("/health")
def health():
    """Liveness + database reachability (used by docker compose and uptime checks)."""
    from sqlalchemy import text

    from .db import engine

    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        db = "ok"
    except Exception as exc:  # noqa: BLE001
        db = f"error: {type(exc).__name__}"
    return {"status": "ok" if db == "ok" else "degraded", "db": db, "version": app.version}


@app.get("/data-status")
def data_status(db=Depends(get_db)):
    """Public: which data the site is showing right now, so every page can say it plainly.
    mode: live (persistent database, real feeds) | demo (baked SYNTHETIC database).
    price_feed: connected (a real Agmarknet pull succeeded) | no_key | not_yet | failing."""
    from sqlalchemy import func, select

    from .models import DataSourceRun, Price

    s = get_settings()
    last_real = db.scalar(select(func.max(Price.date)).where(Price.source != "synthetic"))
    synthetic = bool(db.scalar(select(func.count()).select_from(Price).where(Price.source == "synthetic")))
    last_run = db.scalar(select(DataSourceRun).where(DataSourceRun.source == "agmarknet")
                         .order_by(DataSourceRun.id.desc()).limit(1))
    if not s.data_gov_api_key:
        feed = "no_key"
    elif last_run is None:
        feed = "not_yet"
    else:
        feed = "connected" if last_run.status == "success" or last_real else "failing"
    return {"mode": "live" if s.live_catch_up else "demo", "price_feed": feed, "latest_real_price_date": last_real,
            "synthetic_prices": synthetic, "trucks": "simulated" if s.demo_mode else "real"}
