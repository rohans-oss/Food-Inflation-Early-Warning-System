"""AgriPulse API entrypoint: `uvicorn agripulse_api.main:app --reload`."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from tracking.hub import hub

from .routers import admin, auth, lots, mandis, prices, roles, trips


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.jwt_secret in ("", "change-me-in-.env") and not settings.database_url.startswith("sqlite"):
        raise RuntimeError("Set JWT_SECRET in .env before running against a real database")
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

for r in (auth, admin, mandis, prices, lots, trips, roles):
    app.include_router(r.router)


# Driver PWA served same-origin with the API at /driver/ (works on a phone over one URL).
_pwa = Path(__file__).resolve().parents[3] / "apps" / "driver-pwa"
if _pwa.is_dir():
    app.mount("/driver", StaticFiles(directory=_pwa, html=True), name="driver-pwa")


@app.get("/health")
def health():
    return {"status": "ok"}
