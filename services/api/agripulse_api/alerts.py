"""Rule-based alerts (V1): price spike, vehicle delay, unexpected stop, pickup,
arrival and delivery. In-app always; email when SMTP is configured; SMS through an
optional webhook adapter. English and Kannada.

Copy lives in i18n/alerts.json. The Kannada strings were machine-drafted and are marked
unreviewed there; get a native speaker to check them before a field pilot (docs/alerts.md).
"""
import json
import logging
from functools import lru_cache
from pathlib import Path
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Alert, Forecast, Lot, Mandi, Shipment, Trip, User

log = logging.getLogger("agripulse.alerts")

SEVERITY = {
    "price_spike": "warning",
    "vehicle_delay": "warning",
    "unexpected_stop": "warning",
    "picked_up": "info",
    "vehicle_arrived": "info",
    "delivered": "info",
    "incoming_vehicle": "info",
}
LANGS = ("en", "kn")


@lru_cache
def messages() -> dict:
    """Alert copy lives in i18n/alerts.json, one block per language."""
    return json.loads((Path(__file__).parent / "i18n" / "alerts.json").read_text(encoding="utf-8"))


def render(kind: str, lang: str, **params) -> tuple[str, str]:
    m = messages()
    t = m.get(lang, {}).get(kind) or m["en"][kind]
    return t["title"].format(**params), t["body"].format(**params)


def notify(db: Session, user: User, kind: str, dedupe_key: str, **params) -> Alert | None:
    """Create one alert per (user, dedupe_key). Returns None if it already exists."""
    key = f"{user.id}:{dedupe_key}"
    if db.scalar(select(Alert.id).where(Alert.dedupe_key == key)):
        return None
    lang = user.preferred_lang if user.preferred_lang in LANGS else "en"
    title, body = render(kind, lang, **params)
    alert = Alert(user_id=user.id, kind=kind, severity=SEVERITY[kind], title=title, body=body,
                  lang=lang, dedupe_key=key, channels={"in_app": "sent"})
    try:
        with db.begin_nested():
            db.add(alert)
    except IntegrityError:
        return None
    alert.channels = {**alert.channels, "email": _send_email(user, title, body), "sms": _send_sms(user, body)}
    try:
        from tracking.hub import hub

        hub.publish(f"user:{user.id}", {"type": "alert", "title": title, "body": body, "kind": kind})
    except Exception:  # hub not started (CLI / tests)
        pass
    return alert


def _send_email(user: User, title: str, body: str) -> str:
    s = get_settings()
    if not s.smtp_host:
        return "not_configured"
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = f"[AgriPulse] {title}", s.smtp_from, user.email
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        log.warning("email failed: %s", exc)
        return "failed"


def _send_sms(user: User, body: str) -> str:
    """Adapter: POST {to, text} JSON to SMS_WEBHOOK_URL (wire it to your SMS/WhatsApp provider)."""
    s = get_settings()
    if not s.sms_webhook_url or not user.phone:
        return "not_configured"
    try:
        httpx.post(s.sms_webhook_url, content=json.dumps({"to": user.phone, "text": body}),
                   headers={"Content-Type": "application/json"}, timeout=10).raise_for_status()
        return "sent"
    except httpx.HTTPError as exc:
        log.warning("sms failed: %s", exc)
        return "failed"


# ------------------------------------------------------------------ who hears about what


def trip_audience(db: Session, trip: Trip) -> list[User]:
    """Farmers with lots on the trip, the FPO desk, the fleet owner(s)."""
    users: dict[int, User] = {}
    if trip.shipment_id:
        for lot in db.scalars(select(Lot).where(Lot.shipment_id == trip.shipment_id)):
            users[lot.farmer_id] = lot.farmer
        shipment = db.get(Shipment, trip.shipment_id)
        if shipment and shipment.org_id:
            for u in db.scalars(select(User).where(User.org_id == shipment.org_id, User.role == "fpo")):
                users[u.id] = u
    if trip.fleet_org_id:
        for u in db.scalars(select(User).where(User.org_id == trip.fleet_org_id, User.role == "fleet_owner")):
            users[u.id] = u
    return [u for u in users.values() if u.is_active]


def mandi_traders(db: Session, mandi_id: int) -> list[User]:
    return list(db.scalars(select(User).where(User.role == "trader", User.mandi_id == mandi_id, User.is_active.is_(True))))


def spike_alerts(db: Session) -> dict:
    """After each forecast run: alert users whose mandis cross the spike threshold."""
    s = get_settings()
    latest = {}
    for f in db.scalars(select(Forecast).where(Forecast.horizon_weeks == 2).order_by(Forecast.issue_date)):
        latest[f.mandi_id] = f  # keeps the newest per mandi
    hot = {mid: f for mid, f in latest.items() if f.spike_prob >= s.spike_alert_probability}
    if not hot:
        return {"hot_mandis": 0, "alerts": 0}

    audience: dict[int, set[int]] = {}
    for u in db.scalars(select(User).where(User.is_active.is_(True))):
        mids: set[int] = set()
        if u.role == "buyer":
            mids = set(u.watch_mandi_ids or [])
        elif u.role in ("policy", "admin"):
            mids = set(hot)
        elif u.role == "trader" and u.mandi_id:
            mids = {u.mandi_id}
        elif u.role == "farmer":
            mids = {sh.mandi_id for sh in db.scalars(
                select(Shipment).join(Lot, Lot.shipment_id == Shipment.id).where(Lot.farmer_id == u.id))}
        elif u.role == "fpo" and u.org_id:
            mids = {sh.mandi_id for sh in db.scalars(select(Shipment).where(Shipment.org_id == u.org_id))}
        audience[u.id] = mids & set(hot)

    n = 0
    for uid, mids in audience.items():
        user = db.get(User, uid)
        for mid in mids:
            f = hot[mid]
            mandi = db.get(Mandi, mid)
            if notify(db, user, "price_spike", f"spike:{mid}:{f.issue_date}", mandi=mandi.name,
                      prob=round(f.spike_prob * 100), threshold=round(s.spike_threshold_pct),
                      p10=round(f.p10), p50=round(f.p50), p90=round(f.p90),
                      synthetic=" [Synthetic model]" if f.trained_on_synthetic else ""):
                n += 1
    db.commit()
    return {"hot_mandis": len(hot), "alerts": n}
