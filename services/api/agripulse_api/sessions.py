"""Pre-V3 B-3: server-side sessions so a login can be revoked.

- Every sign-in creates a UserSession; both tokens carry its id (`sid`).
- Every authenticated request (HTTP and WebSocket) checks the session: revoked or unknown -> 401 at once.
- Refresh tokens rotate: each refresh retires the old one. Presenting a retired refresh token is treated as theft
  and revokes the whole session, except the same token again within REUSE_GRACE (two tabs refreshing at once).
- Revocations are written to audit_log (entity "user", field "sessions"): who, when, why, how many.
Tokens issued before B-3 have no sid and are rejected: everyone signs in once after the upgrade.
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import AuditLog, User, UserSession
from .security import create_access_token, create_refresh_token, decode_token

REUSE_GRACE = timedelta(seconds=30)


class SessionInvalid(Exception):
    """The token's session is unknown, revoked, or the token predates sessions."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def issue(user: User, sess: UserSession) -> tuple[str, str]:
    return (create_access_token(user.id, user.role, user.org_id, sess.id),
            create_refresh_token(user.id, sess.id, sess.refresh_jti))


def start(db: Session, user: User, user_agent: str = "") -> tuple[str, str]:
    """New sign-in: a new session. Returns (access_token, refresh_token)."""
    sess = UserSession(id=secrets.token_hex(16), user_id=user.id, user_agent=(user_agent or "")[:200],
                       refresh_jti=secrets.token_hex(16))
    db.add(sess)
    db.flush()
    return issue(user, sess)


def active_session(db: Session, payload: dict) -> UserSession:
    sid = payload.get("sid")
    sess = db.get(UserSession, sid) if sid else None
    if sess is None or sess.revoked_at is not None or str(sess.user_id) != str(payload.get("sub")):
        raise SessionInvalid("Session signed out or revoked")
    return sess


def user_from_access_token(db: Session, token: str) -> User | None:
    """For WebSockets: the user behind an access token, or None if the token/session/user is not valid."""
    try:
        payload = decode_token(token)
        active_session(db, payload)
    except Exception:
        return None
    u = db.get(User, int(payload["sub"]))
    return u if u and u.is_active else None


def session_is_active(db: Session, token: str) -> bool:
    try:
        active_session(db, decode_token(token))
        return True
    except Exception:
        return False


def rotate(db: Session, payload: dict) -> UserSession:
    """Refresh: validate the refresh token against its session and rotate it. Raises SessionInvalid."""
    sess = active_session(db, payload)
    jti, now = payload.get("jti"), _now()
    if jti == sess.refresh_jti:
        sess.prev_refresh_jti, sess.refresh_jti, sess.rotated_at = jti, secrets.token_hex(16), now
        return sess
    if jti == sess.prev_refresh_jti and sess.rotated_at and now - _aware(sess.rotated_at) <= REUSE_GRACE:
        return sess  # a second tab refreshed with the same token a moment later: hand it the current pair
    # A retired refresh token came back: someone else has a copy. End the session for everyone holding it.
    revoke(db, sess.user_id, actor_id=None, reason="refresh token reused (possible theft)", via="reuse_detection",
           only=sess.id)
    raise SessionInvalid("Refresh token already used; session revoked")


def revoke(db: Session, user_id: int, actor_id: int | None, reason: str | None, via: str,
           only: str | None = None, keep: str | None = None) -> int:
    """Revoke the user's active sessions (all, `only` one, or all but `keep`). Writes one audit_log row.
    Returns how many sessions were revoked."""
    now = _now()
    q = select(UserSession.id).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if only:
        q = q.where(UserSession.id == only)
    if keep:
        q = q.where(UserSession.id != keep)
    ids = list(db.scalars(q))
    if ids:
        db.execute(update(UserSession).where(UserSession.id.in_(ids))
                   .values(revoked_at=now, revoked_by=actor_id, revoke_reason=(reason or "")[:200] or None))
    db.add(AuditLog(entity="user", entity_id=user_id, field="sessions", from_state="active", to_state="revoked",
                    actor_id=actor_id, at=now,
                    details={"via": via, "reason": reason or None, "sessions_revoked": len(ids),
                             **({"session": only} if only else {})}))
    db.flush()
    return len(ids)


def active_counts(db: Session) -> dict[int, int]:
    """user_id -> number of active (not revoked, refresh not expired) sessions."""
    from .config import get_settings

    since = _now() - timedelta(days=get_settings().jwt_refresh_days)
    rows = db.execute(select(UserSession.user_id, func.count()).where(
        UserSession.revoked_at.is_(None), func.coalesce(UserSession.rotated_at, UserSession.created_at) >= since)
        .group_by(UserSession.user_id)).all()
    return {u: n for u, n in rows}
