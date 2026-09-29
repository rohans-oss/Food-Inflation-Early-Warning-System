"""GPS ingestion: store points, update position/ETA, fire geofence events.

Rules enforced here (project rule 4 - privacy):
  - points are accepted only while the trip is in_progress AND the driver has consented
  - re-sent points (offline buffer replays) are idempotent: PK (trip_id, recorded_at)
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.models import GeofenceEvent, GpsPoint, Lot, Mandi, Trip

from .geo import haversine_km, remaining_along_route_km
from .hub import hub


MAX_PLAUSIBLE_KMPH = 130.0  # loaded goods vehicle; anything above is a bad fix


class TrackingNotActive(Exception):
    pass


def _parse_ts(v) -> datetime:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, (int, float)):
        dt = datetime.fromtimestamp(v / 1000 if v > 1e12 else v, tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _has_event(db: Session, trip_id: int, event: str, since: datetime | None = None) -> bool:
    q = select(GeofenceEvent.id).where(GeofenceEvent.trip_id == trip_id, GeofenceEvent.event == event)
    if since is not None:
        q = q.where(GeofenceEvent.occurred_at >= since)
    return db.scalar(q.limit(1)) is not None


def add_event(db: Session, trip: Trip, event: str, at: datetime, lat=None, lon=None, **details) -> GeofenceEvent:
    ev = GeofenceEvent(trip_id=trip.id, event=event, occurred_at=at, lat=lat, lon=lon, details=details)
    db.add(ev)
    db.flush()
    hub.publish(f"trip:{trip.id}", {"type": "event", "trip_id": trip.id, "event": event, "at": at, "details": details})
    return ev


def process_points(db: Session, trip: Trip, points: list[dict], now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    if trip.status != "in_progress" or trip.consent_given_at is None:
        raise TrackingNotActive("Tracking is only accepted during an active trip with driver consent")

    mandi = db.get(Mandi, trip.mandi_id)
    parsed = []
    for p in points:
        try:
            ts = _parse_ts(p["recorded_at"] if "recorded_at" in p else p["t"])
            lat, lon = float(p["lat"]), float(p["lon"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if ts > now + timedelta(minutes=2) or (trip.started_at and ts < trip.started_at - timedelta(minutes=1)):
            continue
        parsed.append((ts, lat, lon, p.get("speed_kmph"), p.get("accuracy_m")))
    parsed.sort()

    existing = set(
        db.scalars(select(GpsPoint.recorded_at).where(
            GpsPoint.trip_id == trip.id, GpsPoint.recorded_at.in_([x[0] for x in parsed]))).all()
    ) if parsed else set()
    existing = {e if e.tzinfo else e.replace(tzinfo=timezone.utc) for e in existing}

    events, accepted, dupes = [], 0, 0
    prev = (trip.last_seen_at, trip.last_lat, trip.last_lon)
    for ts, lat, lon, speed, acc in parsed:
        if ts in existing:
            dupes += 1
            continue
        existing.add(ts)
        if speed is None and prev[0] is not None and ts > prev[0]:
            secs = (ts - prev[0]).total_seconds()
            # GPS jitter over short gaps makes derived speed meaningless; only derive over >= 3 s
            if secs >= 3:
                speed = haversine_km(prev[1], prev[2], lat, lon) / (secs / 3600)
        if speed is not None and not (0 <= speed <= MAX_PLAUSIBLE_KMPH):
            speed = None  # a truck can't do this: bad fix or clock skew, keep the point but drop the speed
        db.add(GpsPoint(trip_id=trip.id, recorded_at=ts, lat=lat, lon=lon, speed_kmph=speed, accuracy_m=acc,
                        received_at=now, is_simulated=trip.is_simulated))
        accepted += 1
        # Only move "current position" forward in time (offline replays can arrive late).
        if trip.last_seen_at is None or ts >= trip.last_seen_at:
            trip.last_lat, trip.last_lon, trip.last_seen_at = lat, lon, ts
            trip.last_speed_kmph = speed
        prev = (ts, lat, lon)
        events += _geofence(db, trip, mandi, ts, lat, lon, speed, s)

    if accepted:
        _update_eta(db, trip, mandi, s)
        db.flush()
        payload = live_payload(trip)
        hub.publish(f"trip:{trip.id}", {"type": "position", **payload})
        if trip.fleet_org_id:
            hub.publish(f"fleet:{trip.fleet_org_id}", {"type": "position", **payload})
        hub.publish(f"mandi:{trip.mandi_id}", {"type": "position", **payload})
    return {"accepted": accepted, "duplicates": dupes, "events": events}


def _geofence(db, trip: Trip, mandi: Mandi, ts, lat, lon, speed, s) -> list[str]:
    out = []
    d_pickup_m = haversine_km(lat, lon, trip.origin_lat, trip.origin_lon) * 1000
    in_pickup = d_pickup_m <= s.pickup_radius_m
    in_mandi = mandi.lat is not None and haversine_km(lat, lon, mandi.lat, mandi.lon) * 1000 <= mandi.geofence_radius_m

    if not in_pickup and not _has_event(db, trip.id, "left_pickup_zone"):
        add_event(db, trip, "left_pickup_zone", ts, lat, lon)
        out.append("left_pickup_zone")
    if in_mandi and not _has_event(db, trip.id, "reached_mandi"):
        add_event(db, trip, "reached_mandi", ts, lat, lon)
        out.append("reached_mandi")
        _on_arrival(db, trip, mandi, ts)

    moving = speed is not None and speed >= s.stop_speed_kmph
    if moving or in_pickup or in_mandi:
        trip.stopped_since = None
    elif speed is None:
        pass  # unknown speed (bad fix / first point): neither start nor reset the stop timer
    else:
        if trip.stopped_since is None:
            trip.stopped_since = ts
        stopped_for = ts - trip.stopped_since
        if stopped_for >= timedelta(minutes=s.unexpected_stop_minutes) and not _has_event(
            db, trip.id, "unexpected_stop", since=trip.stopped_since
        ):
            minutes = int(stopped_for.total_seconds() // 60)
            add_event(db, trip, "unexpected_stop", ts, lat, lon, minutes=minutes)
            out.append("unexpected_stop")
            _on_stop(db, trip, minutes)
    return out


def _update_eta(db: Session, trip: Trip, mandi: Mandi, s) -> None:
    if trip.last_lat is None or mandi.lat is None:
        return
    if trip.route_geometry:
        rem, off = remaining_along_route_km(trip.last_lat, trip.last_lon, trip.route_geometry)
        rem += off  # get back onto the route first
    else:
        rem = haversine_km(trip.last_lat, trip.last_lon, mandi.lat, mandi.lon) * s.fallback_road_factor
    trip.remaining_km = round(rem, 2)
    if trip.planned_distance_km and trip.planned_duration_min and trip.planned_distance_km > 0:
        avg_kmph = trip.planned_distance_km / (trip.planned_duration_min / 60)
    else:
        avg_kmph = s.fallback_speed_kmph
    trip.eta_at = trip.last_seen_at + timedelta(hours=rem / max(avg_kmph, 5))
    _check_delay(db, trip, s)


def _check_delay(db: Session, trip: Trip, s) -> None:
    if not (trip.started_at and trip.planned_duration_min and trip.eta_at):
        return
    planned = trip.started_at + timedelta(minutes=trip.planned_duration_min)
    late = (trip.eta_at - planned).total_seconds() / 60
    if late >= s.delay_alert_minutes:
        from agripulse_api.alerts import notify, trip_audience

        mandi = db.get(Mandi, trip.mandi_id)
        bucket = int(late // 60)  # at most one alert per extra hour of delay
        for u in trip_audience(db, trip):
            notify(db, u, "vehicle_delay", f"delay:{trip.id}:{bucket}", vehicle=trip.vehicle.registration,
                   mandi=mandi.name, minutes=int(late), eta=_local(trip.eta_at))


def _on_arrival(db: Session, trip: Trip, mandi: Mandi, ts: datetime) -> None:
    from agripulse_api.alerts import mandi_traders, notify, trip_audience

    for u in trip_audience(db, trip) + mandi_traders(db, mandi.id):
        notify(db, u, "vehicle_arrived", f"arrived:{trip.id}", vehicle=trip.vehicle.registration,
               mandi=mandi.name, time=_local(ts))


def _on_stop(db: Session, trip: Trip, minutes: int) -> None:
    from agripulse_api.alerts import notify, trip_audience

    for u in trip_audience(db, trip):
        if u.role in ("fleet_owner", "fpo"):
            notify(db, u, "unexpected_stop", f"stop:{trip.id}:{trip.stopped_since.isoformat()}",
                   vehicle=trip.vehicle.registration, minutes=minutes)


def _local(dt: datetime) -> str:
    return (dt + timedelta(hours=5, minutes=30)).strftime("%I:%M %p").lstrip("0") + " IST"


def live_payload(trip: Trip) -> dict:
    return {
        "trip_id": trip.id,
        "status": trip.status,
        "lat": trip.last_lat,
        "lon": trip.last_lon,
        "speed_kmph": round(trip.last_speed_kmph, 1) if trip.last_speed_kmph is not None else None,
        "last_seen_at": trip.last_seen_at,
        "remaining_km": trip.remaining_km,
        "eta_at": trip.eta_at,
        "eta_local": _local(trip.eta_at) if trip.eta_at else None,
        "vehicle": trip.vehicle.registration if trip.vehicle else None,
        "mandi_id": trip.mandi_id,
        "load_tons": trip.load_tons,
        "is_simulated": trip.is_simulated,
        "stopped_since": trip.stopped_since,
    }


def public_payload(db: Session, trip: Trip) -> dict:
    """What an unauthenticated share-link viewer may see: position, ETA, lot status. Nothing else."""
    lots = db.scalars(select(Lot).where(Lot.shipment_id == trip.shipment_id)).all() if trip.shipment_id else []
    return {
        "status": trip.status,
        "lat": trip.last_lat if trip.status == "in_progress" else None,
        "lon": trip.last_lon if trip.status == "in_progress" else None,
        "last_seen_at": trip.last_seen_at,
        "remaining_km": trip.remaining_km,
        "eta_at": trip.eta_at,
        "eta_local": _local(trip.eta_at) if trip.eta_at else None,
        "lots": [{"lot_id": lot.id, "status": lot.status} for lot in lots],
        "is_simulated": trip.is_simulated,
    }
