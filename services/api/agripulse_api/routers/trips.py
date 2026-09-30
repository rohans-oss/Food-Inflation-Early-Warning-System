"""Trips: assignment, the driver's lifecycle, QR chain of custody, GPS ingest, live views."""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracking import routing
from tracking.engine import TrackingNotActive, _local, add_event, live_payload, pause_tracking, process_points, public_payload
from tracking.hub import hub

from ..alerts import mandi_traders, notify, trip_audience
from ..config import get_settings
from .. import db as dbmod
from ..db import get_db
from ..lifecycle import move
from ..models import AuditLog, GeofenceEvent, GpsPoint, Lot, Mandi, Shipment, Trip, User, Vehicle
from ..rbac import forbid, get_current_user, require
from ..scoping import scoped_trips, trip_filter
from ..security import decode_token, new_token

router = APIRouter(tags=["trips & tracking"])


def trip_out(db: Session, t: Trip, viewer: User | None = None) -> dict:
    driver = db.get(User, t.driver_id) if t.driver_id else None
    out = {
        **live_payload(t),
        "id": t.id,
        "shipment_id": t.shipment_id,
        "mandi": t.mandi.name,
        "mandi_lat": t.mandi.lat,
        "mandi_lon": t.mandi.lon,
        "origin_lat": t.origin_lat,
        "origin_lon": t.origin_lon,
        "driver": {"id": driver.id, "name": driver.full_name} if driver else None,
        "consent_given_at": t.consent_given_at,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
        "pickup_scanned_at": t.pickup_scanned_at,
        "delivery_scanned_at": t.delivery_scanned_at,
        "planned_distance_km": t.planned_distance_km,
        "planned_duration_min": t.planned_duration_min,
        "route_source": t.route_source,
        "tracking_on": t.status == "in_progress" and t.consent_given_at is not None,
    }
    if viewer is not None and viewer.role == "driver" and viewer.id == t.driver_id:
        # The driver's phone shows this as a QR for the trader to scan at the mandi.
        out["delivery_qr_token"] = t.delivery_qr_token if t.delivery_scanned_at is None else None
    if viewer is not None and viewer.role in ("farmer", "fpo", "admin", "fleet_owner") and t.share_token:
        if t.share_expires_at and t.share_expires_at > datetime.now(timezone.utc):
            out["share_url"] = f"{get_settings().public_base_url}/track/{t.share_token}"
    return out


def get_scoped_trip(db: Session, user: User, trip_id: int) -> Trip:
    t = db.scalar(select(Trip).where(Trip.id == trip_id, trip_filter(user)))
    if t is None:
        raise forbid()
    return t


# ------------------------------------------------------------------ assignment (fleet owner)


class TripIn(BaseModel):
    shipment_id: int
    vehicle_id: int
    driver_id: int


@router.post("/trips", status_code=201)
def assign_trip(body: TripIn, db: Session = Depends(get_db), user: User = Depends(require("trips:assign"))):
    t = make_trip(db, user, body.shipment_id, body.vehicle_id, body.driver_id)
    db.commit()
    db.refresh(t)
    return trip_out(db, t, user)


def make_trip(db: Session, user: User, shipment_id: int, vehicle_id: int, driver_id: int, **audit) -> Trip:
    """Assign vehicle + driver to a booked shipment (no commit). Used by POST /trips and by accepting a V3-1
    return-load proposal, so both go through the same checks."""
    body = TripIn(shipment_id=shipment_id, vehicle_id=vehicle_id, driver_id=driver_id)
    sh = db.get(Shipment, body.shipment_id)
    fleet_org = user.org_id
    if user.role == "fpo":
        raise HTTPException(403, "FPOs book a fleet; the fleet owner assigns vehicle and driver")
    if sh is None or (user.role != "admin" and sh.fleet_org_id != fleet_org):
        raise forbid()
    if user.role == "admin":
        fleet_org = sh.fleet_org_id
    if sh.status != "booked":
        raise HTTPException(409, f"Shipment is {sh.status}, expected booked")
    if db.scalar(select(Trip.id).where(Trip.shipment_id == sh.id, Trip.status.not_in(["declined", "cancelled"]))):
        raise HTTPException(409, "Shipment already has an active trip")
    v = db.get(Vehicle, body.vehicle_id)
    d = db.get(User, body.driver_id)
    if v is None or v.org_id != fleet_org or d is None or d.role != "driver" or d.org_id != fleet_org or not d.is_active:
        raise HTTPException(400, "Vehicle and driver must belong to the booked fleet")
    lots = list(sh.lots)
    tons = sum(lot.quantity_tons for lot in lots)
    if tons > v.capacity_tons:
        raise HTTPException(400, f"Load {tons:.1f} t exceeds vehicle capacity {v.capacity_tons:.1f} t")
    # V1: one pickup point = tonnage-weighted centre of the lots (multi-stop routing is V3)
    o_lat = sum(lot.pickup_lat * lot.quantity_tons for lot in lots) / tons
    o_lon = sum(lot.pickup_lon * lot.quantity_tons for lot in lots) / tons
    mandi = db.get(Mandi, sh.mandi_id)
    r = routing.route(o_lat, o_lon, mandi.lat, mandi.lon)
    t = Trip(
        shipment_id=sh.id, vehicle_id=v.id, driver_id=d.id, fleet_org_id=fleet_org, mandi_id=mandi.id,
        origin_lat=o_lat, origin_lon=o_lon, load_tons=round(tons, 3), is_simulated=v.is_simulated or sh.is_simulated,
        pickup_qr_token=new_token(), delivery_qr_token=new_token(),
        planned_distance_km=r.distance_km, planned_duration_min=r.duration_min, route_geometry=r.geometry,
        route_source=r.source, remaining_km=r.distance_km,
    )
    db.add(t)
    db.flush()
    db.add(AuditLog(entity="trip", entity_id=t.id, from_state=None, to_state="assigned", actor_id=user.id,
                    details={"vehicle": v.registration, "driver_id": d.id, "route_source": r.source, **audit}))
    from .bookings import on_trip_assigned  # a farmer's booking is confirmed by assigning its truck

    on_trip_assigned(db, sh, t)
    return t


@router.get("/trips")
def list_trips(status: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("trips:read"))):
    q = scoped_trips(user).order_by(Trip.created_at.desc())
    if status:
        q = q.where(Trip.status.in_(status.split(",")))
    return [trip_out(db, t, user) for t in db.scalars(q.limit(500))]


@router.get("/trips/{trip_id}")
def get_trip(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:read"))):
    t = get_scoped_trip(db, user, trip_id)
    out = trip_out(db, t, user)
    out["route"] = t.route_geometry
    out["events"] = [
        {"event": e.event, "at": e.occurred_at, "lat": e.lat, "lon": e.lon, "details": e.details}
        for e in db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == t.id).order_by(GeofenceEvent.occurred_at))
    ]
    pts = db.scalars(select(GpsPoint).where(GpsPoint.trip_id == t.id).order_by(GpsPoint.recorded_at)).all()
    step = max(1, len(pts) // 500)  # thin long tracks for the map
    out["track"] = [[p.lon, p.lat] for p in pts[::step]]
    out["points_count"] = len(pts)
    return out


# ------------------------------------------------------------------ driver lifecycle


def _driver_trip(db: Session, user: User, trip_id: int) -> Trip:
    t = db.get(Trip, trip_id)
    if t is None or t.driver_id != user.id:
        raise forbid()
    return t


@router.post("/trips/{trip_id}/accept")
def accept(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    t = _driver_trip(db, user, trip_id)
    move(db, t, "accepted", user.id)
    db.commit()
    return trip_out(db, t, user)


@router.post("/trips/{trip_id}/decline")
def decline(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    t = _driver_trip(db, user, trip_id)
    move(db, t, "declined", user.id)
    db.commit()
    return trip_out(db, t, user)


class ConsentIn(BaseModel):
    consent: bool = Field(description="Driver agrees to share location during this trip only")


@router.post("/trips/{trip_id}/consent")
def consent(trip_id: int, body: ConsentIn, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    t = _driver_trip(db, user, trip_id)
    if t.status not in ("accepted", "in_progress"):
        raise HTTPException(409, "Accept the trip first")
    t.consent_given_at = datetime.now(timezone.utc) if body.consent else None
    if not body.consent:
        t.tracking_paused_at = t.tracking_pause_reason = None
    db.commit()
    return trip_out(db, t, user)


@router.post("/trips/{trip_id}/start")
def start(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    t = _driver_trip(db, user, trip_id)
    if t.consent_given_at is None:
        raise HTTPException(409, "Location consent is required before starting the trip")
    move(db, t, "in_progress", user.id)
    now = datetime.now(timezone.utc)
    t.started_at = now
    t.share_token = new_token()
    t.share_expires_at = now + timedelta(hours=get_settings().share_link_hours)
    if t.shipment:
        move(db, t.shipment, "in_transit", user.id, trip_id=t.id)
    db.commit()
    return trip_out(db, t, user)


class ScanIn(BaseModel):
    token: str


@router.post("/trips/{trip_id}/scan/pickup")
def scan_pickup(trip_id: int, body: ScanIn, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """Driver scans the QR shown on the farmer's / FPO's screen when loading."""
    t = _driver_trip(db, user, trip_id)
    if t.status != "in_progress":
        raise HTTPException(409, "Start the trip before scanning at pickup")
    if body.token != t.pickup_qr_token:
        raise HTTPException(400, "QR code does not match this trip")
    if t.pickup_scanned_at is None:
        now = datetime.now(timezone.utc)
        t.pickup_scanned_at = now
        add_event(db, t, "picked_up", now, t.last_lat, t.last_lon)
        link = f"{get_settings().public_base_url}/track/{t.share_token}"
        for lot in db.scalars(select(Lot).where(Lot.shipment_id == t.shipment_id)):
            move(db, lot, "in_transit", user.id, trip_id=t.id, via="pickup_qr")
            notify(db, lot.farmer, "picked_up", f"pickup:{t.id}:{lot.id}", lot=lot.id, vehicle=t.vehicle.registration,
                   mandi=t.mandi.name, link=link)
        for trader in mandi_traders(db, t.mandi_id):
            notify(db, trader, "incoming_vehicle", f"incoming:{t.id}", vehicle=t.vehicle.registration,
                   tons=t.load_tons, mandi=t.mandi.name, eta=trip_out(db, t)["eta_local"] or "pending")
    db.commit()
    return trip_out(db, t, user)


@router.post("/trips/{trip_id}/scan/delivery")
def scan_delivery(trip_id: int, body: ScanIn, db: Session = Depends(get_db), user: User = Depends(require("arrivals:confirm"))):
    """Trader scans the QR on the driver's phone at the mandi gate."""
    t = db.get(Trip, trip_id)
    if t is None or t.mandi_id != user.mandi_id:
        raise forbid()
    if body.token != t.delivery_qr_token:
        raise HTTPException(400, "QR code does not match this trip")
    if t.status != "in_progress":
        raise HTTPException(409, f"Trip is {t.status}")
    if t.delivery_scanned_at is None:
        now = datetime.now(timezone.utc)
        t.delivery_scanned_at = now
        add_event(db, t, "delivered", now, t.last_lat, t.last_lon, confirmed_by=user.id)
        if not db.scalar(select(GeofenceEvent.id).where(GeofenceEvent.trip_id == t.id, GeofenceEvent.event == "reached_mandi")):
            # QR proves arrival even if GPS never entered the geofence (e.g. phone died)
            add_event(db, t, "reached_mandi", now, t.last_lat, t.last_lon, source="qr_scan")
            for u in trip_audience(db, t):
                notify(db, u, "vehicle_arrived", f"arrived:{t.id}", vehicle=t.vehicle.registration, mandi=t.mandi.name,
                       time=_local(now))
        for lot in db.scalars(select(Lot).where(Lot.shipment_id == t.shipment_id)):
            move(db, lot, "at_mandi", user.id, trip_id=t.id, via="delivery_qr")
    db.commit()
    return trip_out(db, t, user)


@router.post("/trips/{trip_id}/end")
def end(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """Driver ends the trip. V3-3 (backlog 4): only after the trader scanned the delivery QR, so a lot can't be left
    in transit forever. To stop sharing location before that, the driver withdraws consent; a load that won't be
    delivered is closed by the fleet owner (POST /trips/{id}/close)."""
    t = _driver_trip(db, user, trip_id)
    if t.shipment_id and t.delivery_scanned_at is None:
        raise HTTPException(409, "The trader hasn't scanned your delivery QR yet. To stop sharing location now, untick "
                                 "location sharing; if the load won't be delivered, your fleet owner closes the trip.")
    move(db, t, "completed", user.id)
    t.ended_at = datetime.now(timezone.utc)
    t.tracking_paused_at = t.tracking_pause_reason = None
    t.share_expires_at = min(t.share_expires_at or t.ended_at, t.ended_at + timedelta(hours=2))
    db.commit()
    hub.publish(f"trip:{t.id}", {"type": "status", "trip_id": t.id, "status": t.status})
    return trip_out(db, t, user)


class CloseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/trips/{trip_id}/close")
def close_trip(trip_id: int, body: CloseIn, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    """Fleet owner / admin ends a trip that won't be delivered (breakdown, rejected load). Tracking stops; the lots
    are NOT marked delivered - they stay in transit for the FPO / trader to resolve. Audited with the reason."""
    t = db.get(Trip, trip_id)
    if t is None or (user.role != "admin" and t.fleet_org_id != user.org_id):
        raise forbid()
    if t.status not in ("assigned", "accepted", "in_progress"):
        raise HTTPException(409, f"Trip is {t.status}")
    move(db, t, "cancelled", user.id, reason=body.reason, delivered=t.delivery_scanned_at is not None)
    now = datetime.now(timezone.utc)
    t.ended_at = now
    t.tracking_paused_at = t.tracking_pause_reason = None
    t.share_expires_at = min(t.share_expires_at or now, now)
    db.commit()
    hub.publish(f"trip:{t.id}", {"type": "status", "trip_id": t.id, "status": t.status})
    return trip_out(db, t, user)


class PointIn(BaseModel):
    recorded_at: datetime
    lat: float
    lon: float
    speed_kmph: float | None = None
    accuracy_m: float | None = None


class PointsIn(BaseModel):
    points: list[PointIn] = Field(max_length=5000)


@router.post("/trips/{trip_id}/points")
def post_points(trip_id: int, body: PointsIn, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """HTTP ingest - used by the PWA to flush its offline buffer."""
    t = _driver_trip(db, user, trip_id)
    try:
        out = process_points(db, t, [p.model_dump() for p in body.points])
    except TrackingNotActive as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    return {**out, "trip": live_payload(t)}


class PauseIn(BaseModel):
    reason: str = "screen_off"
    at: datetime | None = None


@router.post("/trips/{trip_id}/pause")
def post_pause(trip_id: int, body: PauseIn, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """Pre-V3 B-2: the browser app is about to be hidden and can't record location. The next fix resumes tracking.
    Lets the monitor and the farmer's view say "location paused on the phone" instead of "vehicle stopped"."""
    t = _driver_trip(db, user, trip_id)
    now = datetime.now(timezone.utc)
    at = min(body.at, now) if body.at else now
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    try:
        changed = pause_tracking(db, t, body.reason, at)
    except TrackingNotActive as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    db.commit()
    return {"paused": True, "changed": changed, "trip": live_payload(t)}


@router.post("/trips/{trip_id}/share")
def reshare(trip_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:read"))):
    t = get_scoped_trip(db, user, trip_id)
    if user.role not in ("farmer", "fpo", "fleet_owner", "admin"):
        raise HTTPException(403, "Not allowed")
    if t.status != "in_progress":
        raise HTTPException(409, "Share links exist only for trips in progress")
    t.share_token = new_token()
    t.share_expires_at = datetime.now(timezone.utc) + timedelta(hours=get_settings().share_link_hours)
    db.commit()
    return {"share_url": f"{get_settings().public_base_url}/track/{t.share_token}", "expires_at": t.share_expires_at}


@router.get("/public/track/{share_token}")
def public_track(share_token: str, db: Session = Depends(get_db)):
    """No login. Expiring, unguessable token. Position, ETA and lot status only."""
    t = db.scalar(select(Trip).where(Trip.share_token == share_token))
    if t is None:
        raise HTTPException(404, "Unknown link")
    if t.share_expires_at is None or t.share_expires_at < datetime.now(timezone.utc):
        raise HTTPException(410, "This tracking link has expired")
    return public_payload(db, t)


# ------------------------------------------------------------------ websockets


def _ws_auth(db: Session, token: str | None, ticket: str | None) -> tuple[User | None, str | None]:
    """(user, session id) for a WebSocket. Preferred: ?ticket= (60 s, single use, V3-3). ?token= (the access token)
    still works for older clients (e.g. installed Android builds) but puts a long-lived token in the URL."""
    from ..sessions import redeem_ws_ticket, user_from_access_token

    if ticket:
        return redeem_ws_ticket(db, ticket)
    if token:
        u = user_from_access_token(db, token)
        if u is None:
            return None, None
        try:
            return u, decode_token(token).get("sid")
        except Exception:
            return None, None
    return None, None


def _ws_user(db: Session, token: str | None, ticket: str | None = None) -> User | None:
    return _ws_auth(db, token, ticket)[0]


def _ws_session_ok(sid: str | None) -> bool:
    from ..sessions import sid_is_active

    with dbmod.SessionLocal() as db:
        return sid_is_active(db, sid)


SESSION_RECHECK_S = 60  # live viewers: a revoked session is disconnected within this many seconds


@router.websocket("/ws/driver/{trip_id}")
async def ws_driver(ws: WebSocket, trip_id: int, token: str | None = None, ticket: str | None = None):
    """Driver phone streams {"points": [...]} or a single point; server acks with counts."""
    await ws.accept()
    with dbmod.SessionLocal() as db:
        user, sid = _ws_auth(db, token, ticket)
        t = db.get(Trip, trip_id)
        if user is None or t is None or t.driver_id != user.id:
            await ws.close(code=4403)
            return
    try:
        while True:
            msg = await ws.receive_json()
            pts = msg.get("points") or [msg]
            if not await asyncio.to_thread(_ws_session_ok, sid):  # B-3: revoked -> stop taking this phone's GPS
                await ws.close(code=4401)
                return

            def work():
                with dbmod.SessionLocal() as db:
                    trip = db.get(Trip, trip_id)
                    try:
                        out = process_points(db, trip, pts)
                        db.commit()
                        return {"ok": True, **out, "trip": live_payload(trip)}
                    except TrackingNotActive as exc:
                        return {"ok": False, "error": str(exc)}

            await ws.send_json(_jsonable(await asyncio.to_thread(work)))
    except WebSocketDisconnect:
        return


async def _pump(ws: WebSocket, channels: list[str], initial: list[dict], sid: str | None = None,
                recheck_s: float | None = None):
    """Forward hub messages. With a session id, the session is re-checked every `recheck_s` seconds (B-3)."""
    q = hub.subscribe(*channels)
    loop = asyncio.get_running_loop()
    recheck_s = SESSION_RECHECK_S if recheck_s is None else recheck_s
    next_check = loop.time() + recheck_s
    try:
        for m in initial:
            await ws.send_json(_jsonable(m))
        while True:
            getter = asyncio.create_task(q.get())
            recv = asyncio.create_task(ws.receive_text())
            timeout = max(0.0, next_check - loop.time()) if sid else None
            done, pending = await asyncio.wait({getter, recv}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            if sid and loop.time() >= next_check:
                if not await asyncio.to_thread(_ws_session_ok, sid):
                    await ws.close(code=4401)
                    return
                next_check = loop.time() + recheck_s
            if recv in done:
                recv.result()  # raises WebSocketDisconnect when the client leaves
                continue
            if getter in done:
                await ws.send_json(getter.result())
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(q)


@router.websocket("/ws/trips/{trip_id}")
async def ws_trip(ws: WebSocket, trip_id: int, token: str | None = None, share: str | None = None,
                  ticket: str | None = None):
    """Live view for one trip. Auth with a JWT (?token=) or a public share token (?share=)."""
    await ws.accept()
    with dbmod.SessionLocal() as db:
        t = db.get(Trip, trip_id)
        ok = False
        if t is not None and share:
            ok = t.share_token == share and t.share_expires_at and t.share_expires_at > datetime.now(timezone.utc)
            initial = [{"type": "position", **public_payload(db, t)}] if ok else []
            public = True
        else:
            user, sid = _ws_auth(db, token, ticket)
            ok = bool(user and t and db.scalar(select(Trip.id).where(Trip.id == trip_id, trip_filter(user))))
            initial = [{"type": "position", **live_payload(t)}] if ok else []
            public = False
    if not ok:
        await ws.close(code=4403)
        return
    if public:
        await _pump_public(ws, trip_id, initial)
    else:
        await _pump(ws, [f"trip:{trip_id}"], initial, sid=sid)


@router.websocket("/ws/public/{share_token}")
async def ws_public(ws: WebSocket, share_token: str):
    """Live feed for the public tracking link: same reduced payload as GET /public/track/{token}."""
    await ws.accept()
    with dbmod.SessionLocal() as db:
        t = db.scalar(select(Trip).where(Trip.share_token == share_token))
        ok = t is not None and t.share_expires_at is not None and t.share_expires_at > datetime.now(timezone.utc)
        initial = [{"type": "position", **public_payload(db, t)}] if ok else []
        trip_id = t.id if t else None
    if not ok:
        await ws.close(code=4410)
        return
    await _pump_public(ws, trip_id, initial)


async def _pump_public(ws: WebSocket, trip_id: int, initial: list[dict]):
    """Public viewers get a re-computed public payload on each update, never the raw one."""
    q = hub.subscribe(f"trip:{trip_id}")
    try:
        for m in initial:
            await ws.send_json(_jsonable(m))
        while True:
            getter = asyncio.create_task(q.get())
            recv = asyncio.create_task(ws.receive_text())
            done, pending = await asyncio.wait({getter, recv}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            if recv in done:
                recv.result()
                continue
            getter.result()

            def snapshot():
                with dbmod.SessionLocal() as db:
                    t = db.get(Trip, trip_id)
                    if t.share_expires_at is None or t.share_expires_at < datetime.now(timezone.utc):
                        return None
                    return {"type": "position", **public_payload(db, t)}

            snap = await asyncio.to_thread(snapshot)
            if snap is None:
                await ws.close(code=4410)
                return
            await ws.send_json(_jsonable(snap))
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(q)


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str | None = None, ticket: str | None = None):
    """Role-scoped live feed: fleet owners get their fleet, traders their mandi, others their alerts."""
    await ws.accept()
    with dbmod.SessionLocal() as db:
        user, sid = _ws_auth(db, token, ticket)
        if user is None:
            await ws.close(code=4401)
            return
        channels = [f"user:{user.id}"]
        if user.role == "fleet_owner" and user.org_id:
            channels.append(f"fleet:{user.org_id}")
        if user.role == "trader" and user.mandi_id:
            channels.append(f"mandi:{user.mandi_id}")
        if user.role in ("farmer", "fpo", "driver", "lender"):
            channels += [f"trip:{tid}" for tid in db.scalars(
                select(Trip.id).where(trip_filter(user), Trip.status == "in_progress"))]
        if user.role in ("admin", "policy", "buyer"):
            channels += [f"mandi:{mid}" for mid in db.scalars(select(Mandi.id))]
    await _pump(ws, channels, [{"type": "hello", "channels": len(channels)}], sid=sid)


def _jsonable(d):
    import json

    return json.loads(json.dumps(d, default=str))
