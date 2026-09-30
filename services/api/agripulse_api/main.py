"""AgriPulse API entrypoint: `uvicorn agripulse_api.main:app --reload`."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from tracking.hub import hub

from .routers import admin, auth, bookings, graph, lots, mandis, prices, proposals, receipts, roles, scenarios, trips


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

for r in (auth, admin, mandis, prices, lots, bookings, receipts, trips, roles, graph, proposals, scenarios):
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
