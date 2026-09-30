"""Once-a-minute checks for trips whose phone went quiet (network drop, app killed).
A silent phone can't report that it stopped, so the server has to notice."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.models import Trip

from .engine import _check_delay, _has_event, _on_stop, add_event


def check_stale_trips(db: Session, now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    flagged = 0
    for trip in db.scalars(select(Trip).where(Trip.status == "in_progress")):
        if trip.last_seen_at is None:
            continue
        silent = now - trip.last_seen_at
        if silent >= timedelta(minutes=s.unexpected_stop_minutes) and not _has_event(
            db, trip.id, "unexpected_stop", since=trip.last_seen_at
        ):
            if _has_event(db, trip.id, "reached_mandi"):
                continue
            minutes = int(silent.total_seconds() // 60)
            reason = "phone_paused" if trip.tracking_paused_at is not None else "no_signal"
            add_event(db, trip, "unexpected_stop", now, trip.last_lat, trip.last_lon, minutes=minutes, reason=reason,
                      **({"pause_reason": trip.tracking_pause_reason} if reason == "phone_paused" else {}))
            trip.stopped_since = trip.stopped_since or trip.last_seen_at
            _on_stop(db, trip, minutes)
            flagged += 1
        # the ETA can't be earlier than now: push it out and re-check lateness
        if trip.eta_at and trip.eta_at < now and not _has_event(db, trip.id, "reached_mandi"):
            trip.eta_at = now + timedelta(minutes=5)
            # V3-3 (backlog 17): a paused phone's position is stale, so a delay computed from it isn't evidence;
            # the "tracking paused" alert already tells the fleet owner. Delay alerts resume with the next fix.
            if trip.tracking_paused_at is None:
                _check_delay(db, trip, s)
    db.commit()
    return {"flagged": flagged}
