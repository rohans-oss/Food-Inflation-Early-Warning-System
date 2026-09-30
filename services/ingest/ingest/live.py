"""LIVE mode (real prices on a persistent Postgres; trucks in the demo are still simulated and labelled so).

Free hosting sleeps when idle, so a clock-only scheduler misses days. `catch_up()` runs in the background on every
start and does whatever is overdue: weather, today's Agmarknet pull, the history probe + backfill, and a real-data
retrain when the readiness monitor says at least one mandi has enough real history. Nothing here ever uses synthetic
rows: when real data is not ready there is simply no forecast, and the UI shows today's real prices instead.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from agripulse_api.config import get_settings
from agripulse_api.db import SessionLocal
from agripulse_api.models import DataSourceRun, Forecast, Weather

log = logging.getLogger("agripulse.live")


def _last_success(db, source: str):
    return db.scalar(select(func.max(DataSourceRun.finished_at)).where(DataSourceRun.source == source,
                                                                       DataSourceRun.status == "success"))


def _older_than(ts, hours: float) -> bool:
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(hours=hours)


def _step(name, fn):
    with SessionLocal() as db:
        try:
            out = fn(db)
            log.info("[live] %s: %s", name, str(out)[:1500])
            return out
        except BaseException as exc:  # SystemExit from train() included: log and carry on
            db.rollback()
            log.warning("[live] %s failed: %s", name, exc)
            return None


def real_price_ready(db) -> bool:
    from agripulse_api.readiness import mandi_price_ready

    return any(mandi_price_ready(db).values())


def retrain_if_ready(db, force: bool = False) -> dict:
    """Display model on REAL rows only (never --synthetic here): retrain when the model file is missing (free hosts
    wipe the disk on restart) or a week old, then write forecasts when today's are missing."""
    if not real_price_ready(db):
        return {"skipped": "no mandi has enough real price history yet (config/readiness.toml)"}
    from agripulse_ml.predict import run_job
    from agripulse_ml.train import train

    s = get_settings()
    d = Path(s.model_dir)
    files = [p for p in d.glob("*") if p.is_file()] if d.exists() else []
    model_age_h = min(((datetime.now().timestamp() - p.stat().st_mtime) / 3600 for p in files), default=None)
    out: dict = {}
    if force or model_age_h is None or model_age_h > 24 * 7:
        r = train(db, allow_synthetic=False, n_folds=3)
        out.update({"model": r["model_version"], "provenance": r["data_provenance"],
                    "vs_naive_pinball_pct": r["vs_naive_pinball_pct"]})
    latest = db.scalar(select(func.max(Forecast.created_at)).where(Forecast.data_provenance != "synthetic"))
    if out or _older_than(latest, 20):
        out["forecasts"] = run_job(db).get("written")
    return out or {"skipped": "model and forecasts are fresh"}


def catch_up() -> None:
    from .agmarknet import run_daily
    from .history import KeepAwake, backfill, last_probe, probe
    from .weather import run_nasa_power, run_open_meteo

    with SessionLocal() as db:
        weather_rows = db.scalar(select(func.count()).select_from(Weather).where(Weather.source != "synthetic")) or 0
        need_agm = _older_than(_last_success(db, "agmarknet"), 5)
        need_om = _older_than(_last_success(db, "open_meteo"), 3)
        need_probe = last_probe(db) is None
    with KeepAwake():
        if need_om:
            _step("open_meteo", lambda db: run_open_meteo(db))
        _step("nasa_power", lambda db: run_nasa_power(db, days_back=800 if weather_rows < 1000 else 10))
        if need_agm:
            _step("agmarknet", lambda db: run_daily(db, raw_dir=None))
        if need_probe:
            _step("history_probe", probe)
        _step("history_backfill", backfill)
        _step("retrain", retrain_if_ready)


def start_catch_up() -> threading.Thread:
    root = logging.getLogger("agripulse")  # make [live] / probe findings visible in the host's log stream
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(h)
    root.setLevel(logging.INFO)
    t = threading.Thread(target=catch_up, name="live-catch-up", daemon=True)
    t.start()
    return t
