"""Scheduled jobs (APScheduler). Run as its own process: `python -m ingest.scheduler`.

Times are IST. Agmarknet publishes through the day, so it is pulled twice; the
forecast + spike-alert job runs after the evening pull.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from agripulse_api.db import SessionLocal

log = logging.getLogger("agripulse.scheduler")
TZ = "Asia/Kolkata"


def _safe(name, fn):
    def run():
        with SessionLocal() as db:
            try:
                out = fn(db)
                log.info("%s ok: %s", name, out)
            except Exception:  # failures are already recorded in data_source_runs
                log.exception("%s failed", name)

    run.__name__ = name
    return run


def job_agmarknet(db):
    from .agmarknet import run_daily

    return run_daily(db)


def job_open_meteo(db):
    from .weather import run_open_meteo

    return run_open_meteo(db)


def job_nasa_power(db):
    from .weather import run_nasa_power

    return run_nasa_power(db, days_back=10)


def job_forecast_and_alerts(db):
    from agripulse_api.alerts import spike_alerts
    from agripulse_ml.predict import run_job

    out = run_job(db)
    out["alerts"] = spike_alerts(db)
    return out


def job_retrain_weekly(db):
    """Refit the quantile model on real history (never synthetic) and refresh the backtest report."""
    from agripulse_ml.train import train

    from .runs import tracked_run

    with tracked_run(db, "retrain") as run:
        try:
            r = train(db, allow_synthetic=False)
        except SystemExit as exc:  # train() exits when there is no real history yet; log it as a failed run
            raise RuntimeError(str(exc)) from None
        run.rows = r["rows"]
        run.details = {"model_version": r["model_version"], "vs_naive_pinball_pct": r["vs_naive_pinball_pct"]}
        return run.details


def job_trip_monitor(db):
    from tracking.monitor import check_stale_trips

    return check_stale_trips(db)


JOBS = [
    ("agmarknet_midday", job_agmarknet, CronTrigger(hour=13, minute=10, timezone=TZ)),
    ("agmarknet_evening", job_agmarknet, CronTrigger(hour=19, minute=40, timezone=TZ)),
    ("open_meteo_hourly", job_open_meteo, CronTrigger(minute=5, timezone=TZ)),
    ("nasa_power_daily", job_nasa_power, CronTrigger(hour=6, minute=20, timezone=TZ)),
    ("forecast_daily", job_forecast_and_alerts, CronTrigger(hour=20, minute=30, timezone=TZ)),
    ("retrain_weekly", job_retrain_weekly, CronTrigger(day_of_week="sun", hour=2, minute=15, timezone=TZ)),
    ("trip_monitor", job_trip_monitor, CronTrigger(minute="*", timezone=TZ)),
]


def _add_jobs(sched):
    for name, fn, trigger in JOBS:
        sched.add_job(_safe(name, fn), trigger, id=name, max_instances=1, coalesce=True, misfire_grace_time=3600)
    return sched


def start_scheduler() -> BackgroundScheduler:
    """In-process variant used when ENABLE_SCHEDULER=true (dev)."""
    sched = _add_jobs(BackgroundScheduler(timezone=TZ))
    sched.start()
    return sched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("starting scheduler with %d jobs", len(JOBS))
    _add_jobs(BlockingScheduler(timezone=TZ)).start()
