"""B-2: replay one 65 km farm -> Bengaluru trip through the REAL tracking engine + monitor under driver behaviours.
Phone records a fix every 5 s only while the app is visible (browsers stop geolocation for hidden pages).
Offline (dead zone) fixes ARE recorded and arrive late. Behaviour timings are assumptions, stated per scenario."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from agripulse_api.models import Alert, GeofenceEvent, Mandi, Trip
from tests.test_tracking import FARM, journey, seed_forecasts  # noqa: F401 (fixture)
from tracking.engine import process_points
from tracking.geo import haversine_km
from tracking.monitor import check_stale_trips

pytest_plugins = ["tests.conftest"]
OUT = Path(__file__).with_name("replay.json")  # run from repo root: pytest --rootdir=. -p no:cacheprovider docs/b2-investigation/test_gap_replay.py
KMPH, STOP_AT, STOP_MIN = 35.0, 55, 20  # driving speed, tea stop after 55 min of driving, 20 min long

# (start_min, end_min) windows in trip minutes. hidden = app not visible (no fixes); offline = fixes buffered.
SCENARIOS = {
    "A_screen_on_mounted": {"hidden": [], "offline": [(30, 33), (90, 93)],
        "note": "phone mounted, app open all trip, wake lock held; two 3-min dead zones"},
    "B_current_app_one_call": {"hidden": "B", "offline": [(30, 33), (90, 93)],
        "note": "current code: a 2-min call at 25 min releases the wake lock and it is never re-acquired; the screen "
                "then auto-locks 1 min after each time the driver touches the phone (tea stop, arrival QR)"},
    "C_wakelock_fixed_calls_and_tea_stop": {"hidden": [(25, 28), (80, 83), (STOP_AT, STOP_AT + STOP_MIN)],
        "offline": [(30, 33), (90, 93)], "note": "wake lock works; two 3-min calls; phone locked during the tea stop"},
    "D_driver_uses_maps_navigation": {"hidden": "D", "offline": [(30, 33), (90, 93)],
        "note": "Google Maps navigation in the foreground all trip; app visible only at start and on arrival (QR)"},
}


def windows(name, total, end):
    s = SCENARIOS[name]["hidden"]
    if s == "B":  # visible until the call; after it, visible only 1 min after each touch
        w, t = [], 25
        for touch in (STOP_AT + STOP_MIN - 2, end - 1):  # driver opens the app at the end of the tea stop, and on arrival
            w.append((t, touch)); t = touch + 1
        return w
    if s == "D":
        return [(1, end - 1)]
    return s


def inside(t, ws):
    return any(a <= t < b for a, b in ws)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_replay(db, journey, name):
    t = db.get(Trip, journey["trip"]["id"])
    blr = db.scalar(select(Mandi).where(Mandi.name == "Binny Mill (FF&V) Bengaluru APMC"))
    km = haversine_km(FARM[0], FARM[1], blr.lat, blr.lon)
    drive_min = km / KMPH * 60
    total = drive_min + STOP_MIN
    t0 = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
    t.mandi_id, t.route_geometry, t.status = blr.id, None, "in_progress"
    t.consent_given_at, t.started_at = t0, t0
    t.planned_distance_km, t.planned_duration_min = km * 1.3, int(km * 1.3 / KMPH * 60)
    db.commit()

    def pos(m):  # straight line, paused during the tea stop
        d = min(m, STOP_AT) + max(0, m - STOP_AT - STOP_MIN)
        f = min(d / drive_min, 1.0)
        return FARM[0] + (blr.lat - FARM[0]) * f, FARM[1] + (blr.lon - FARM[1]) * f

    expected, recorded, queue, stale_minutes, km_in_gaps = 0, [], [], 0, 0.0
    arrive_min = next(m / 12 for m in range(int(total * 12) + 12)
                      if haversine_km(*pos(m / 12), blr.lat, blr.lon) * 1000 <= blr.geofence_radius_m)
    end = arrive_min + 10  # driver shows the delivery QR ~10 min after arriving (app visible then)
    step = 5 / 60
    hidden, offline = windows(name, total, end), SCENARIOS[name]["offline"]
    m, next_monitor, last_pos, last_fix_min = 0.0, 1.0, pos(0), 0.0
    while m <= end:
        now = t0 + timedelta(minutes=m)
        visible = not inside(m, hidden) or m >= end - 1
        p = pos(m)
        expected += 1
        if visible:
            fix = {"recorded_at": now, "lat": p[0], "lon": p[1], "accuracy_m": 10}
            recorded.append(m)
            queue.append(fix)
            if not inside(m, offline):
                process_points(db, t, queue, now=now); db.commit(); queue = []
            if m - last_fix_min > 1:
                km_in_gaps += haversine_km(*last_pos, *p)
            last_pos, last_fix_min = p, m
        if m >= next_monitor:
            check_stale_trips(db, now=now)
            db.refresh(t)
            if t.last_seen_at is None or (now - t.last_seen_at.replace(tzinfo=timezone.utc)) > timedelta(minutes=2):
                stale_minutes += 1
            next_monitor += 1
        m += step
    ev = {e.event: e for e in db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id))}
    stops = db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id, GeofenceEvent.event == "unexpected_stop")).all()
    alerts = [a.kind for a in db.scalars(select(Alert)) if a.kind in ("unexpected_stop", "vehicle_delay")]
    gaps = [b - a for a, b in zip(recorded, recorded[1:])]
    reached = ev.get("reached_mandi")
    res = {
        "note": SCENARIOS[name]["note"], "route_km_straight": round(km, 1), "trip_min_to_arrival": round(arrive_min, 1),
        "fixes_expected": expected, "fixes_recorded": len(recorded),
        "fixes_lost_pct": round(100 * (1 - len(recorded) / expected), 1),
        "longest_gap_min": round(max(gaps), 1),
        "farmer_view_stale_pct_of_minutes": round(100 * stale_minutes / int(end), 1),
        "km_drawn_as_straight_line": round(km_in_gaps, 1),
        "moving_minutes_without_fix": round(sum(1 for i in range(int(end)) if inside(i, hidden) and not (STOP_AT <= i < STOP_AT + STOP_MIN) and i < arrive_min), 0),
        "false_no_signal_stop_events": sum(1 for s in stops if s.details.get("reason") == "no_signal"),
        "stop_or_delay_alerts": len(alerts),
        "reached_mandi_late_by_min": round((reached.occurred_at.replace(tzinfo=timezone.utc) - t0).total_seconds() / 60 - arrive_min, 1)
        if reached else None,
    }
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    data[name] = res
    OUT.write_text(json.dumps(data, indent=1))
