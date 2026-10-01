"""PERSISTENT public demo (DATA_MODE=demo + a Postgres DATABASE_URL): the demo lives in its own schema (`demo`) of that
database, so accounts people create survive restarts, while the SAMPLE (synthetic) prices stay apart from any real data.
On every start: extend the sample history to today (same fixed draw, see synthetic.load_into_db), write today's sample
forecasts with the model baked into the image (no retraining), build the mandi graph once, then start demo traffic."""
import logging
import threading
from datetime import date

from sqlalchemy import func, select

from agripulse_api.db import SessionLocal
from agripulse_api.models import Forecast, GraphEdge, Price

log = logging.getLogger("agripulse.demo")


def refresh() -> dict:
    out: dict = {}
    with SessionLocal() as db:
        last = db.scalar(select(func.max(Price.date)).where(Price.source == "synthetic"))
        if last is None or last < date.today():
            from agripulse_ml.synthetic import load_into_db

            out["sample_prices"] = load_into_db(db)
        fc = db.scalar(select(func.max(Forecast.issue_date)))
        if fc is None or fc < date.today() - __import__("datetime").timedelta(days=1):
            from agripulse_ml.predict import run_job

            out["forecasts"] = run_job(db).get("written")
        if not db.scalar(select(func.count()).select_from(GraphEdge)):
            try:
                from agripulse_ml.graph.build import run_job as graph_job

                out["graph"] = graph_job(db)
            except Exception as exc:  # optional
                out["graph"] = f"skipped: {exc}"
    return out


def _run() -> None:
    try:
        log.warning("[demo] sample data refresh: %s", str(refresh())[:500])
    except Exception:
        log.exception("[demo] sample data refresh failed")
    from agripulse_api.routers.bookings import start_demo_traffic

    start_demo_traffic(delay_s=2)


def start() -> threading.Thread:
    t = threading.Thread(target=_run, name="demo-refresh", daemon=True)
    t.start()
    return t
