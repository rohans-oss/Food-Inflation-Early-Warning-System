"""Web Push (VAPID) for driver phones: a new trip request reaches the phone even when the screen is locked.

Keys: VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY from the environment if set, else generated ONCE and kept in app_settings
(same idea as the stable session key), so a restart doesn't invalidate every phone's subscription.

Delivery is best-effort and asynchronous: `send_to_user` hands the message to a background thread (the push
service call can take a second; the farmer's request must not wait for it). Subscriptions the push service says are
gone (404 / 410) are deleted. Tests replace `SENDER` to capture messages without network.
"""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AppSetting, PushSubscription

log = logging.getLogger("agripulse.webpush")
_lock = threading.Lock()
_cached: tuple[str, str] | None = None


def _generate() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01, b64urlencode

    v = Vapid01()
    v.generate_keys()
    public = b64urlencode(v.public_key.public_bytes(serialization.Encoding.X962,
                                                    serialization.PublicFormat.UncompressedPoint))
    private = b64urlencode(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    return private, public


def vapid_keys(db: Session) -> tuple[str, str]:
    """(private, public) as base64url. Env wins; else app_settings; else generated and stored."""
    global _cached
    env_priv, env_pub = os.environ.get("VAPID_PRIVATE_KEY"), os.environ.get("VAPID_PUBLIC_KEY")
    if env_priv and env_pub:
        return env_priv, env_pub
    if _cached is not None:
        return _cached
    with _lock:
        rows = {r.key: r.value for r in db.scalars(select(AppSetting).where(
            AppSetting.key.in_(["vapid_private", "vapid_public"])))}
        if len(rows) < 2:
            priv, pub = _generate()
            for k, v in (("vapid_private", priv), ("vapid_public", pub)):
                row = db.get(AppSetting, k)
                if row is None:
                    db.add(AppSetting(key=k, value=v))
                else:
                    row.value = v
            db.commit()
            rows = {"vapid_private": priv, "vapid_public": pub}
        _cached = (rows["vapid_private"], rows["vapid_public"])
        return _cached


def reset_cache() -> None:
    global _cached
    _cached = None


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def subscribe(db: Session, user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> PushSubscription:
    h = endpoint_hash(endpoint)
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == h))
    if sub is None:
        sub = PushSubscription(user_id=user_id, endpoint=endpoint, endpoint_hash=h, p256dh=p256dh, auth=auth,
                               user_agent=(user_agent or "")[:300] or None)
        db.add(sub)
    else:  # same browser, maybe another account now: the subscription follows whoever subscribed last
        sub.user_id, sub.p256dh, sub.auth, sub.failures = user_id, p256dh, auth, 0
    return sub


def has_subscription(db: Session, user_id: int) -> bool:
    return db.scalar(select(PushSubscription.id).where(PushSubscription.user_id == user_id)) is not None


def _real_sender(sub: dict, data: str, private_key: str, claims: dict) -> int:
    """Returns the push service's HTTP status (201 = accepted)."""
    from pywebpush import WebPushException, webpush

    try:
        r = webpush(sub, data=data, vapid_private_key=private_key, vapid_claims=dict(claims), ttl=300, timeout=10,
                    headers={"Urgency": "high"})
        return getattr(r, "status_code", 201)
    except WebPushException as exc:
        return getattr(exc.response, "status_code", 0) or 0


SENDER = _real_sender  # tests replace this


def _claims() -> dict:
    base = get_settings().public_base_url
    return {"sub": base if base.startswith("https://") else "mailto:admin@agripulse.local"}


def send_to_user(db: Session, user_id: int, payload: dict, background: bool = True) -> int:
    """Queue `payload` to every push subscription of the user. Returns how many subscriptions it went to."""
    subs = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all()
    if not subs:
        return 0
    private, _ = vapid_keys(db)
    jobs = [(s.id, {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}) for s in subs]
    data = json.dumps(payload, default=str)

    def run():
        from . import db as dbmod

        results = [(sid, SENDER(info, data, private, _claims())) for sid, info in jobs]
        try:
            with dbmod.SessionLocal() as s:
                for sid, status in results:
                    row = s.get(PushSubscription, sid)
                    if row is None:
                        continue
                    if status in (404, 410):  # the browser dropped this subscription
                        s.delete(row)
                    elif 200 <= status < 300:
                        row.last_ok_at, row.failures = datetime.now(timezone.utc), 0
                    else:
                        row.failures = (row.failures or 0) + 1
                        log.warning("web push to subscription %s failed: HTTP %s", sid, status)
                s.commit()
        except Exception:
            log.exception("recording web push results failed")

    if background:
        threading.Thread(target=run, name=f"push-{user_id}", daemon=True).start()
    else:
        run()
    return len(jobs)
