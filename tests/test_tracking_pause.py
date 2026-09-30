"""Pre-V3 B-2: the browser driver app reports "location paused (screen off)"; the server labels it honestly."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from agripulse_api.models import Alert, GeofenceEvent, Trip, User
from tests.test_tracking import journey, seed_forecasts  # noqa: F401 (fixture)
from tracking.engine import process_points
from tracking.monitor import check_stale_trips

MID = (13.17, 78.07)  # on the road, away from farm and mandi


def _live(db, trip_id, minutes_ago=60):
    t = db.get(Trip, trip_id)
    now = datetime.now(timezone.utc)
    t.status, t.consent_given_at, t.started_at = "in_progress", now - timedelta(minutes=minutes_ago), now - timedelta(minutes=minutes_ago)
    db.commit()
    return t


def _events(db, trip_id, kind):
    return db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == trip_id, GeofenceEvent.event == kind)).all()


def test_pause_needs_an_active_consented_trip_and_the_trips_own_driver(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    r = client.post(f"/trips/{tid}/pause", json={"reason": "screen_off"}, headers=as_role("driver"))
    assert r.status_code == 409  # trip not started: nothing to pause
    _live(db, tid)
    assert client.post(f"/trips/{tid}/pause", json={}, headers=as_role("farmer")).status_code == 403  # drivers only
    assert client.post(f"/trips/{tid}/pause", json={"reason": "bored"}, headers=as_role("driver")).status_code == 422


def test_pause_then_next_fix_resumes_and_old_replays_do_not(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    t = _live(db, tid)
    now = datetime.now(timezone.utc)
    process_points(db, t, [{"recorded_at": now - timedelta(minutes=20), "lat": MID[0], "lon": MID[1], "speed_kmph": 40}], now=now)
    db.commit()
    r = client.post(f"/trips/{tid}/pause", json={"reason": "screen_off", "at": (now - timedelta(minutes=15)).isoformat()},
                    headers=as_role("driver"))
    assert r.status_code == 200 and r.json()["changed"] is True
    assert r.json()["trip"]["tracking_pause_reason"] == "screen_off"
    again = client.post(f"/trips/{tid}/pause", json={"reason": "screen_off"}, headers=as_role("driver")).json()
    assert again["changed"] is False and len(_events(db, tid, "tracking_paused")) == 1  # once per pause

    db.expire_all()
    t = db.get(Trip, tid)
    # an offline replay recorded BEFORE the pause must not end it
    process_points(db, t, [{"recorded_at": now - timedelta(minutes=17), "lat": MID[0], "lon": MID[1], "speed_kmph": 40}], now=now)
    db.commit()
    assert t.tracking_paused_at is not None and not _events(db, tid, "tracking_resumed")
    # a fix recorded after the pause ends it, with the paused minutes
    out = process_points(db, t, [{"recorded_at": now, "lat": MID[0], "lon": MID[1], "speed_kmph": 40}], now=now)
    db.commit()
    assert "tracking_resumed" in out["events"] and t.tracking_paused_at is None and t.tracking_pause_reason is None
    ev = _events(db, tid, "tracking_resumed")[0]
    assert ev.details["minutes"] == 15 and ev.details["reason"] == "screen_off"
    body = client.get(f"/trips/{tid}", headers=as_role("farmer")).json()
    assert body["tracking_paused_since"] is None and {e["event"] for e in body["events"]} >= {"tracking_paused", "tracking_resumed"}


def test_monitor_says_phone_paused_not_vehicle_stopped(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    t = _live(db, tid)
    now = datetime.now(timezone.utc)
    process_points(db, t, [{"recorded_at": now - timedelta(minutes=45), "lat": MID[0], "lon": MID[1], "speed_kmph": 40}], now=now)
    db.commit()
    client.post(f"/trips/{tid}/pause", json={"reason": "screen_off", "at": (now - timedelta(minutes=44)).isoformat()},
                headers=as_role("driver"))
    db.expire_all()
    assert client.get(f"/trips/{tid}", headers=as_role("farmer")).json()["tracking_paused_since"] is not None
    assert check_stale_trips(db, now=now)["flagged"] == 1
    ev = _events(db, tid, "unexpected_stop")[0]
    assert ev.details["reason"] == "phone_paused" and ev.details["pause_reason"] == "screen_off"
    fleet_owner = db.scalar(select(User).where(User.email == "fleet@demo.agripulse"))
    alerts = db.scalars(select(Alert).where(Alert.user_id == fleet_owner.id)).all()
    kinds = {a.kind for a in alerts}
    assert "tracking_paused" in kinds and "unexpected_stop" not in kinds  # the alert says what is actually known
    a = next(a for a in alerts if a.kind == "tracking_paused")
    assert "may still be moving" in a.body or a.lang != "en"


def test_ending_the_trip_or_withdrawing_consent_clears_the_pause(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    _live(db, tid)
    client.post(f"/trips/{tid}/pause", json={"reason": "screen_off"}, headers=as_role("driver"))
    client.post(f"/trips/{tid}/consent", json={"consent": False}, headers=as_role("driver"))
    db.expire_all()
    assert db.get(Trip, tid).tracking_paused_at is None
    _live(db, tid)
    client.post(f"/trips/{tid}/pause", json={"reason": "screen_off"}, headers=as_role("driver"))
    t = db.get(Trip, tid)
    t.delivery_scanned_at = datetime.now(timezone.utc)  # V3-3: a driver ends a trip only after the delivery scan
    db.commit()
    assert client.post(f"/trips/{tid}/end", headers=as_role("driver")).status_code == 200
    db.expire_all()
    assert db.get(Trip, tid).tracking_paused_at is None


def test_pause_alert_copy_exists_in_every_language():
    msgs = json.loads((Path(__file__).resolve().parents[1] / "services/api/agripulse_api/i18n/alerts.json").read_text("utf-8"))
    for lang in ("en", "kn"):
        assert {"title", "body"} <= set(msgs[lang]["tracking_paused"])
        assert "{vehicle}" in msgs[lang]["tracking_paused"]["body"] and "{minutes}" in msgs[lang]["tracking_paused"]["body"]


def test_android_app_origin_is_allowed_by_cors(client):
    r = client.options("/auth/login", headers={"Origin": "https://localhost", "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == "https://localhost"
