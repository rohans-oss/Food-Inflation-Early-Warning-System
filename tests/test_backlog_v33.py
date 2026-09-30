"""V3-3 backlog closures: #4 end-trip guard + fleet close, #6 WebSocket tickets, #11 pinned synthetic draw,
#17 no delay alerts from a paused phone, #19 device list, #21 session clean-up."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from agripulse_api.models import Alert, AuditLog, Lot, Trip, User, UserSession
from tests.test_tracking import journey, seed_forecasts  # noqa: F401 (fixture)


def _live(db, trip_id, minutes_ago=60):
    t = db.get(Trip, trip_id)
    now = datetime.now(timezone.utc)
    t.status, t.consent_given_at, t.started_at = "in_progress", now, now - timedelta(minutes=minutes_ago)
    db.commit()
    return t


# ---- #4
def test_driver_cannot_end_before_delivery_but_fleet_owner_can_close(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    _live(db, tid)
    r = client.post(f"/trips/{tid}/end", headers=as_role("driver"))
    assert r.status_code == 409 and "delivery QR" in r.json()["detail"]
    # the driver can still stop sharing location at any time (privacy rule 4)
    assert client.post(f"/trips/{tid}/consent", json={"consent": False}, headers=as_role("driver")).status_code == 200
    assert client.post(f"/trips/{tid}/close", json={"reason": "x"}, headers=as_role("fleet_owner")).status_code == 422
    assert client.post(f"/trips/{tid}/close", json={"reason": "truck broke down"}, headers=as_role("fpo")).status_code == 403
    r = client.post(f"/trips/{tid}/close", json={"reason": "truck broke down"}, headers=as_role("fleet_owner"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    db.expire_all()
    lot = db.get(Lot, journey["lot"]["id"])
    assert lot.status != "delivered"  # closing never fakes a delivery
    a = db.scalar(select(AuditLog).where(AuditLog.entity == "trip", AuditLog.entity_id == tid, AuditLog.to_state == "cancelled"))
    assert a.details["reason"] == "truck broke down"


# ---- #17
def test_no_delay_alert_from_a_paused_phones_stale_position(client, as_role, db, journey):
    from tracking.engine import process_points
    from tracking.monitor import check_stale_trips

    tid = journey["trip"]["id"]
    t = _live(db, tid, minutes_ago=600)  # started 10 h ago on a short route: any ETA check says "late"
    now = datetime.now(timezone.utc)
    process_points(db, t, [{"recorded_at": now - timedelta(minutes=5), "lat": 13.17, "lon": 78.07, "speed_kmph": 30}], now=now)
    db.query(Alert).delete()
    t.tracking_paused_at, t.tracking_pause_reason = now - timedelta(minutes=4), "screen_off"
    t.eta_at = now - timedelta(minutes=1)
    db.commit()
    check_stale_trips(db, now=now)
    assert not db.scalars(select(Alert).where(Alert.kind == "vehicle_delay")).all()
    t.tracking_paused_at = None
    t.eta_at = now - timedelta(minutes=1)
    db.commit()
    check_stale_trips(db, now=now)
    assert db.scalars(select(Alert).where(Alert.kind == "vehicle_delay")).all()  # not paused: the alert still fires


# ---- #11
def test_synthetic_demo_history_is_the_same_draw_whatever_the_end_date(db):
    from agripulse_api.models import Price
    from agripulse_ml.synthetic import load_into_db

    def prices():
        return {(p.mandi_id, p.date): p.modal_price for p in db.scalars(select(Price).where(Price.source == "synthetic"))}

    load_into_db(db, start=date(2025, 1, 1), end=date(2025, 3, 31))
    db.commit()
    short = prices()
    load_into_db(db, start=date(2025, 1, 1), end=date(2025, 6, 30))
    db.commit()
    longer = prices()
    assert short and max(d for _, d in longer) > max(d for _, d in short)
    assert all(longer[k] == v for k, v in short.items())  # yesterday's demo history is unchanged today


# ---- #19 / #21
def test_device_list_and_signing_out_one_device(client, db):
    a = client.post("/auth/login", json={"email": "farmer@demo.agripulse", "password": "agripulse-demo"},
                    headers={"User-Agent": "Phone"}).json()
    b = client.post("/auth/login", json={"email": "farmer@demo.agripulse", "password": "agripulse-demo"},
                    headers={"User-Agent": "Laptop"}).json()
    ha, hb = ({"Authorization": f"Bearer {x['access_token']}"} for x in (a, b))
    lst = client.get("/auth/sessions", headers=hb).json()
    assert {s["device"] for s in lst} >= {"Phone", "Laptop"} and sum(s["current"] for s in lst) == 1
    phone = next(s for s in lst if s["device"] == "Phone")
    assert client.post(f"/auth/sessions/{phone['id']}/revoke", headers=hb).json()["sessions_revoked"] == 1
    assert client.get("/auth/me", headers=ha).status_code == 401 and client.get("/auth/me", headers=hb).status_code == 200
    other = client.post("/auth/login", json={"email": "driver@demo.agripulse", "password": "agripulse-demo"}).json()
    sid = db.scalar(select(UserSession.id).where(UserSession.user_id == db.scalar(
        select(User.id).where(User.email == "driver@demo.agripulse"))))
    assert client.post(f"/auth/sessions/{sid}/revoke", headers=hb).status_code == 404  # not my session
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {other['access_token']}"}).status_code == 200


def test_old_sessions_are_pruned(client, db):
    from agripulse_api.sessions import prune

    client.post("/auth/login", json={"email": "farmer@demo.agripulse", "password": "agripulse-demo"})
    old = db.scalars(select(UserSession)).all()
    for s in old:
        s.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    fresh = client.post("/auth/login", json={"email": "buyer@demo.agripulse", "password": "agripulse-demo"}).json()
    db.commit()
    n = prune(db)
    assert n >= len(old)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {fresh['access_token']}"}).status_code == 200


# ---- #6
def test_websocket_tickets_are_short_lived_and_single_use(client, as_role, db):
    t = client.post("/auth/ws-ticket", headers=as_role("fleet_owner")).json()
    assert t["expires_in"] == 60
    with client.websocket_connect(f"/ws/live?ticket={t['ticket']}") as ws:
        assert ws.receive_json()["type"] == "hello"
    with client.websocket_connect(f"/ws/live?ticket={t['ticket']}") as ws:  # used once already
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
    access = as_role("fleet_owner")["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/live?ticket={access}") as ws:  # an access token is not a ticket
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
