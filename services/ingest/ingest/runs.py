"""Record run bookkeeping so the Admin freshness monitor and failed-job log have data."""
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agripulse_api.models import DataSourceRun


@contextmanager
def tracked_run(db: Session, source: str):
    """Usage:
        with tracked_run(db, "agmarknet") as run:
            ...; run.rows = n; run.details = {...}
    A raised exception marks the run failed (and is re-raised)."""
    run = DataSourceRun(source=source, status="running", details={})
    db.add(run)
    db.commit()
    try:
        yield run
    except Exception as exc:  # noqa: BLE001 - we log every failure type
        db.rollback()
        run = db.merge(run)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    else:
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
