"""Pre-V3 B-3: server-side sessions, revocation (admin + self-service), refresh rotation, audit trail."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from agripulse_api.config import get_settings
from agripulse_api.models import AuditLog, Trip, User, UserSession
from tests.conftest import PASSWORD, login
from tests.test_tracking import journey, seed_forecasts  # noqa: F401 (fixture)

FARMER, DRIVER = "farmer@demo.agripulse", "driver@demo.agripulse"


def sign_in(client, email, password=PASSWORD, agent="pytest"):
    r = client.post("/auth/login", json={"email": email, "password": password}, headers={"User-Agent": agent})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def me(client, tokens):
    return client.get("/auth/me", headers=bearer(tokens)).status_code


def refresh(client, tokens):
    return client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})


def audit_rows(db, user_id):
    db.expire_all()
    return db.scalars(select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == user_id,
                                             AuditLog.field == "sessions").order_by(AuditLog.id)).all()


def uid(db, email):
    return db.scalar(select(User.id).where(User.email == email))


def test_admin_revoke_fails_the_next_request_and_refresh_and_is_audited(client, as_role, db):
    phone, laptop = sign_in(client, FARMER, agent="phone"), sign_in(client, FARMER, agent="laptop")
    other = sign_in(client, DRIVER)
    assert me(client, phone) == me(client, laptop) == me(client, other) == 200
    users = {u["id"]: u for u in client.get("/admin/users", headers=as_role("admin")).json()}
    assert users[uid(db, FARMER)]["active_sessions"] == 2

    r = client.post(f"/admin/users/{uid(db, FARMER)}/revoke-sessions", json={"reason": "phone reported stolen"},
                    headers=as_role("admin"))
    assert r.status_code == 200 and r.json()["sessions_revoked"] == 2
    # the very next request fails, long before the 30-minute access token would expire
    assert me(client, phone) == 401 and me(client, laptop) == 401
    assert refresh(client, phone).status_code == 401 and refresh(client, laptop).status_code == 401
    # other users are unaffected
    assert me(client, other) == 200 and refresh(client, other).status_code == 200
    # the audit entry: who, when, why, how many
    admin_id = uid(db, "admin@agripulse.local")
    [row] = audit_rows(db, uid(db, FARMER))
    assert row.actor_id == admin_id and row.to_state == "revoked"
    assert row.details == {"via": "admin", "reason": "phone reported stolen", "sessions_revoked": 2}
    assert abs((row.at.replace(tzinfo=row.at.tzinfo or timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 60
    hist = client.get(f"/admin/users/{uid(db, FARMER)}/session-audit", headers=as_role("admin")).json()
    assert hist[0]["reason"] == "phone reported stolen" and hist[0]["actor_id"] == admin_id
    # revoking is not disabling: the farmer can sign in again
    assert me(client, sign_in(client, FARMER)) == 200


def test_reason_is_optional_and_only_admins_may_revoke_others(client, as_role, db):
    victim = sign_in(client, FARMER)
    assert client.post(f"/admin/users/{uid(db, FARMER)}/revoke-sessions", headers=as_role("fleet_owner")).status_code == 403
    assert client.post(f"/admin/users/{uid(db, DRIVER)}/revoke-sessions", headers=bearer(victim)).status_code == 403
    assert me(client, victim) == 200
    r = client.post(f"/admin/users/{uid(db, FARMER)}/revoke-sessions", headers=as_role("admin"))
    assert r.status_code == 200 and audit_rows(db, uid(db, FARMER))[-1].details["reason"] is None
    assert client.post("/admin/users/999999/revoke-sessions", headers=as_role("admin")).status_code == 404


def test_log_out_of_all_devices_is_self_service_and_audited(client, db):
    a, b = sign_in(client, FARMER), sign_in(client, FARMER)
    other = sign_in(client, DRIVER)
    r = client.post("/auth/logout-all", json={"reason": "I think someone has my password"}, headers=bearer(a))
    assert r.status_code == 200 and r.json()["sessions_revoked"] == 2
    assert me(client, a) == 401 and me(client, b) == 401 and refresh(client, b).status_code == 401
    assert me(client, other) == 200
    row = audit_rows(db, uid(db, FARMER))[-1]
    assert row.actor_id == uid(db, FARMER) and row.details["via"] == "self_service"
    assert row.details["reason"] == "I think someone has my password"


def test_logout_ends_only_this_device(client, db):
    a, b = sign_in(client, FARMER), sign_in(client, FARMER)
    assert client.post("/auth/logout", headers=bearer(a)).json()["sessions_revoked"] == 1
    assert me(client, a) == 401 and refresh(client, a).status_code == 401  # a copied token is dead too
    assert me(client, b) == 200 and refresh(client, b).status_code == 200


def test_refresh_rotates_and_a_reused_refresh_token_revokes_the_session(client, db, monkeypatch):
    t0 = sign_in(client, FARMER)
    t1 = refresh(client, t0).json()
    assert t1["refresh_token"] != t0["refresh_token"] and me(client, t1) == 200
    # the same old token again within the grace window (two tabs refreshing at once): accepted
    assert refresh(client, t0).status_code == 200
    # later, the retired token comes back: someone else has a copy -> the whole session is revoked
    from agripulse_api import sessions

    monkeypatch.setattr(sessions, "REUSE_GRACE", timedelta(seconds=-1))
    r = refresh(client, t0)
    assert r.status_code == 401 and "already used" in r.json()["detail"]
    assert me(client, t1) == 401 and refresh(client, t1).status_code == 401
    row = audit_rows(db, uid(db, FARMER))[-1]
    assert row.actor_id is None and row.details["via"] == "reuse_detection" and row.details["sessions_revoked"] == 1


def test_tokens_from_before_sessions_are_rejected(client, db):
    """Tokens without a session id can't be revoked, so they are refused (everyone signs in once after B-3)."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    legacy = jwt.encode({"sub": str(uid(db, FARMER)), "typ": "access", "iat": now, "exp": now + timedelta(minutes=30),
                         "jti": "x", "role": "farmer", "org": None}, s.jwt_secret, algorithm=s.jwt_algorithm)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 401
    forged = jwt.encode({"sub": str(uid(db, DRIVER)), "typ": "access", "iat": now, "exp": now + timedelta(minutes=30),
                         "jti": "x", "sid": db.scalar(select(UserSession.id)) or "nope"}, s.jwt_secret, algorithm=s.jwt_algorithm)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401  # sid of another user


def test_disabled_user_is_still_blocked(client, as_role, db):
    t = sign_in(client, FARMER)
    client.patch(f"/admin/users/{uid(db, FARMER)}", json={"is_active": False}, headers=as_role("admin"))
    assert me(client, t) == 401 and refresh(client, t).status_code == 401


def test_revoked_driver_phone_stops_streaming_gps(client, as_role, db, journey):
    tid = journey["trip"]["id"]
    t = db.get(Trip, tid)
    now = datetime.now(timezone.utc)
    t.status, t.consent_given_at, t.started_at = "in_progress", now, now
    db.commit()
    phone = sign_in(client, DRIVER)
    pt = {"recorded_at": now.isoformat(), "lat": 13.17, "lon": 78.07}
    with client.websocket_connect(f"/ws/driver/{tid}?token={phone['access_token']}") as ws:
        ws.send_json({"points": [pt]})
        assert ws.receive_json()["ok"] is True
        client.post(f"/admin/users/{uid(db, DRIVER)}/revoke-sessions", json={"reason": "left the fleet"},
                    headers=as_role("admin"))
        ws.send_json({"points": [{**pt, "recorded_at": (now + timedelta(seconds=5)).isoformat()}]})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
    # and a revoked token can't open a new socket or post points
    with client.websocket_connect(f"/ws/driver/{tid}?token={phone['access_token']}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4403
    r = client.post(f"/trips/{tid}/points", json={"points": [pt]}, headers=bearer(phone))
    assert r.status_code == 401


def test_live_viewers_are_disconnected_after_revocation(client, as_role, db, journey, monkeypatch):
    from agripulse_api.routers import trips as trips_router

    monkeypatch.setattr(trips_router, "SESSION_RECHECK_S", 0.2)
    farmer = sign_in(client, FARMER)
    with client.websocket_connect(f"/ws/live?token={farmer['access_token']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/auth/logout-all", headers=bearer(farmer))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
