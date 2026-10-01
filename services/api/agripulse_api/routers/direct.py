"""Direct farmer -> driver booking (real accounts, real phones; for the field test).

    driver: PUT /driver/availability (online, district, mandis, truck)  -- a real truck of their company only
    farmer: POST /lots/{id}/driver-request (mandi, district)
            -> every online driver in that district who serves that mandi and has a free truck that fits is offered
               it at once: WebSocket message on their `user:{id}` channel + Web Push to their phones + in-app alert
            -> none: status "no_drivers" and a plain message, nothing left waiting
    driver: POST /driver/requests/{id}/accept   first accept wins (conditional UPDATE); the others are withdrawn
            -> shipment + trip made with the EXISTING make_shipment / make_trip and moved to "accepted";
               from there the usual pickup code / QR, consent, GPS, geofences, delivery QR, weighing and payment.
            POST /driver/requests/{id}/decline  (all offered drivers declined -> request "declined")
    request expires 5 minutes after it was sent if nobody accepted.

Everything here is real: demo (@demo.agripulse) accounts and demo / sample trucks are refused, so a direct trip is
always is_simulated = false and booking_channel = "direct"; the demo autopilot never touches it.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..alerts import notify
from ..db import get_db
from ..demo import is_demo_user
from ..lifecycle import move
from ..models import (AuditLog, DriverAvailability, DriverAvailabilityMandi, Lot, Mandi, Trip, TripRequest,
                      TripRequestOffer, User, Vehicle)
from ..rbac import forbid, get_current_user, require
from .. import webpush

router = APIRouter(tags=["direct booking"])

REQUEST_TTL_S = 300  # a request waits this long for a driver
FRESH_S = 600  # online drivers count while their app checked in within 10 min ...
FRESH_WITH_PUSH_S = 8 * 3600  # ... or 8 h if their phone has Web Push (it can be woken while locked)
ACTIVE_TRIP = ("assigned", "accepted", "in_progress")
OPEN_OFFER = ("notified", "seen")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _norm(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def _publish(channel: str, msg: dict) -> None:
    try:
        from tracking.hub import hub

        hub.publish(channel, msg)
    except Exception:  # hub not running (CLI)
        pass


def _audit(db: Session, req: TripRequest, frm: str | None, to: str, actor_id: int | None, **details) -> None:
    db.add(AuditLog(entity="trip_request", entity_id=req.id, field="status", from_state=frm, to_state=to,
                    actor_id=actor_id, details={k: v for k, v in details.items() if v is not None}))


def _set_status(db: Session, req: TripRequest, to: str, actor_id: int | None, **details) -> None:
    frm = req.status
    req.status = to
    if to not in ("requested", "notified"):
        req.decided_at = req.decided_at or _now()
    _audit(db, req, frm, to, actor_id, **details)


# ------------------------------------------------------------------ availability (driver)


def _availability(db: Session, user: User) -> DriverAvailability:
    a = db.get(DriverAvailability, user.id)
    if a is None:
        a = DriverAvailability(user_id=user.id, online=False, district=user.district)
        db.add(a)
        db.flush()
    return a


def _mandi_ids(db: Session, user_id: int) -> list[int]:
    return list(db.scalars(select(DriverAvailabilityMandi.mandi_id).where(DriverAvailabilityMandi.user_id == user_id)))


def is_fresh(db: Session, a: DriverAvailability, now: datetime | None = None) -> bool:
    now = now or _now()
    seen = _utc(a.last_seen_at)
    if seen is None:
        return False
    window = FRESH_WITH_PUSH_S if webpush.has_subscription(db, a.user_id) else FRESH_S
    return (now - seen).total_seconds() <= window


def _real_vehicles(db: Session, user: User) -> list[Vehicle]:
    if not user.org_id:
        return []
    return list(db.scalars(select(Vehicle).where(Vehicle.org_id == user.org_id, Vehicle.is_simulated.is_(False))
                           .order_by(Vehicle.registration)))


def availability_out(db: Session, user: User, a: DriverAvailability) -> dict:
    from .auth import known_districts

    v = db.get(Vehicle, a.vehicle_id) if a.vehicle_id else None
    fresh = is_fresh(db, a)
    return {"online": a.online, "matched_now": a.online and fresh, "district": a.district, "vehicle_id": a.vehicle_id,
            "vehicle": v.registration if v else None, "capacity_tons": v.capacity_tons if v else None,
            "mandi_ids": _mandi_ids(db, user.id), "last_seen_at": a.last_seen_at,
            "push_subscribed": webpush.has_subscription(db, user.id),
            "stays_online_min": round((FRESH_WITH_PUSH_S if webpush.has_subscription(db, user.id) else FRESH_S) / 60),
            "demo_account": is_demo_user(user),
            "districts": known_districts(db),
            "mandis": [{"id": m.id, "name": m.name, "district": m.district} for m in
                       db.scalars(select(Mandi).where(Mandi.lat.is_not(None)).order_by(Mandi.district, Mandi.name))],
            "vehicles": [{"id": x.id, "registration": x.registration, "capacity_tons": x.capacity_tons}
                         for x in _real_vehicles(db, user)]}


@router.get("/driver/availability")
def get_availability(db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    a = _availability(db, user)
    db.commit()
    return availability_out(db, user, a)


class AvailabilityIn(BaseModel):
    online: bool
    district: str | None = Field(default=None, max_length=80)
    mandi_ids: list[int] = Field(default_factory=list, max_length=30)
    vehicle_id: int | None = None


@router.put("/driver/availability")
def set_availability(body: AvailabilityIn, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """Go online / offline for direct farmer bookings. Online needs a district, at least one mandi and one of the
    company's REAL trucks (sample trucks are refused: direct trips are always real)."""
    from .auth import known_districts

    a = _availability(db, user)
    district = None
    if body.district:
        district = next((d for d in known_districts(db) if _norm(d) == _norm(body.district)), None)
        if district is None:
            raise HTTPException(400, f"Unknown district '{body.district}'")
    mandis = db.scalars(select(Mandi).where(Mandi.id.in_(body.mandi_ids or [-1]))).all()
    if len(mandis) != len(set(body.mandi_ids)):
        raise HTTPException(400, "Unknown mandi")
    v = db.get(Vehicle, body.vehicle_id) if body.vehicle_id else None
    if body.vehicle_id and (v is None or v.org_id != user.org_id):
        raise HTTPException(400, "That truck is not in your company")
    if body.online:
        if is_demo_user(user):
            raise HTTPException(403, "Demo accounts can't take real bookings. Sign up with your own driver account.")
        if not district:
            raise HTTPException(400, "Choose the district you are working in")
        if not mandis:
            raise HTTPException(400, "Choose at least one mandi you will deliver to")
        if v is None:
            raise HTTPException(400, "Choose the truck you are driving today")
        if v.is_simulated:
            raise HTTPException(400, "That is a sample truck. Direct bookings need a real truck: ask your fleet owner to add it.")
    a.online, a.district, a.vehicle_id = body.online, district, v.id if v else None
    a.updated_at = a.last_seen_at = _now()
    db.query(DriverAvailabilityMandi).filter(DriverAvailabilityMandi.user_id == user.id).delete()
    for m in mandis:
        db.add(DriverAvailabilityMandi(user_id=user.id, mandi_id=m.id))
    db.add(AuditLog(entity="driver", entity_id=user.id, field="availability", from_state=None,
                    to_state="online" if body.online else "offline", actor_id=user.id,
                    details={"district": district, "mandi_ids": [m.id for m in mandis], "vehicle_id": a.vehicle_id}))
    db.commit()
    return availability_out(db, user, a)


# ------------------------------------------------------------------ matching


def matching_drivers(db: Session, district: str, mandi_id: int, load_tons: float, now: datetime | None = None
                     ) -> list[tuple[User, Vehicle]]:
    """Online, recently seen drivers in this district who serve this mandi, with a free real truck that can carry the
    load, and no trip of their own in progress. Smallest fitting truck first."""
    now = now or _now()
    rows = db.scalars(select(DriverAvailability).join(
        DriverAvailabilityMandi, DriverAvailabilityMandi.user_id == DriverAvailability.user_id).where(
        DriverAvailability.online.is_(True), DriverAvailabilityMandi.mandi_id == mandi_id)).all()
    busy_drivers = set(db.scalars(select(Trip.driver_id).where(Trip.status.in_(ACTIVE_TRIP))))
    busy_vehicles = set(db.scalars(select(Trip.vehicle_id).where(Trip.status.in_(ACTIVE_TRIP))))
    out = []
    for a in rows:
        if _norm(a.district) != _norm(district) or not is_fresh(db, a, now) or a.user_id in busy_drivers:
            continue
        u = db.get(User, a.user_id)
        v = db.get(Vehicle, a.vehicle_id) if a.vehicle_id else None
        if (u is None or u.role != "driver" or not u.is_active or is_demo_user(u) or v is None or v.is_simulated
                or v.org_id != u.org_id or v.capacity_tons < load_tons or v.id in busy_vehicles):
            continue
        out.append((u, v))
    return sorted(out, key=lambda uv: (uv[1].capacity_tons, uv[0].id))


def expire_due(db: Session) -> int:
    """Requests nobody accepted in time -> expired (the farmer is told, the drivers' cards close)."""
    now = _now()
    due = [r for r in db.scalars(select(TripRequest).where(TripRequest.status == "notified"))
           if r.expires_at is not None and _utc(r.expires_at) <= now]
    for r in due:
        _set_status(db, r, "expired", None)
        r.reason = f"No driver accepted within {REQUEST_TTL_S // 60} minutes."
        _close_offers(db, r, "withdrawn")
        _tell_farmer_no_driver(db, r)
    if due:
        db.commit()
        for r in due:
            _broadcast_closed(db, r)
    return len(due)


def _close_offers(db: Session, req: TripRequest, to: str, except_driver: int | None = None) -> None:
    for o in db.scalars(select(TripRequestOffer).where(TripRequestOffer.request_id == req.id,
                                                       TripRequestOffer.status.in_(OPEN_OFFER))):
        if o.driver_id != except_driver:
            o.status, o.responded_at = to, o.responded_at or _now()


def _tell_farmer_no_driver(db: Session, req: TripRequest) -> None:
    farmer = db.get(User, req.farmer_id)
    if farmer is not None:
        notify(db, farmer, "no_driver", f"nodriver:{req.id}", lot=req.lot_id, reason=req.reason or "")


def _broadcast_closed(db: Session, req: TripRequest) -> None:
    for d in db.scalars(select(TripRequestOffer.driver_id).where(TripRequestOffer.request_id == req.id)):
        _publish(f"user:{d}", {"type": "trip_request_closed", "request_id": req.id, "status": req.status})
    _publish(f"user:{req.farmer_id}", {"type": "trip_request_update", "request_id": req.id, "lot_id": req.lot_id,
                                       "status": req.status, "trip_id": req.trip_id})


# ------------------------------------------------------------------ farmer


def request_out(db: Session, r: TripRequest) -> dict:
    mandi = db.get(Mandi, r.mandi_id)
    offers = db.scalars(select(TripRequestOffer).where(TripRequestOffer.request_id == r.id)).all()
    left = max(0, int((_utc(r.expires_at) - _now()).total_seconds())) if r.expires_at and r.status == "notified" else 0
    out = {"id": r.id, "lot_id": r.lot_id, "status": r.status, "district": r.district, "mandi_id": r.mandi_id,
           "mandi": mandi.name if mandi else None, "load_tons": r.load_tons, "estimated_km": r.estimated_km,
           "estimated_fare": r.estimated_fare, "created_at": r.created_at, "notified_at": r.notified_at,
           "expires_at": r.expires_at, "seconds_left": left, "decided_at": r.decided_at, "reason": r.reason,
           "drivers_notified": len(offers), "drivers_seen": sum(1 for o in offers if o.seen_at),
           "drivers_declined": sum(1 for o in offers if o.status == "declined"), "trip": None}
    if r.trip_id:
        t = db.get(Trip, r.trip_id)
        d = db.get(User, t.driver_id) if t.driver_id else None
        out["trip"] = {"id": t.id, "status": t.status, "vehicle": t.vehicle.registration,
                       "capacity_tons": t.vehicle.capacity_tons, "driver": d.full_name if d else None,
                       "driver_phone": d.phone if d else None, "is_simulated": t.is_simulated,
                       "booking_channel": t.booking_channel}
    return out


def latest_request(db: Session, lot_id: int) -> TripRequest | None:
    return db.scalar(select(TripRequest).where(TripRequest.lot_id == lot_id).order_by(TripRequest.id.desc()).limit(1))


@router.get("/lots/{lot_id}/drivers-available")
def drivers_available(lot_id: int, mandi_id: int | None = None, district: str | None = None,
                      db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    """How many drivers a request would reach right now (shown before the farmer confirms)."""
    from .lots import get_scoped_lot

    lot = get_scoped_lot(db, user, lot_id)
    mandi = db.get(Mandi, mandi_id or lot.preferred_mandi_id or -1)
    if mandi is None:
        raise HTTPException(409, "Choose a mandi first")
    district = district or mandi.district
    return {"mandi": mandi.name, "district": district,
            "drivers": len(matching_drivers(db, district, mandi.id, lot.quantity_tons))}


class DriverRequestIn(BaseModel):
    mandi_id: int | None = None
    district: str | None = Field(default=None, max_length=80)


@router.post("/lots/{lot_id}/driver-request", status_code=201)
def create_request(lot_id: int, body: DriverRequestIn, db: Session = Depends(get_db),
                   user: User = Depends(require("lots:create"))):
    from tracking.routing import road_km

    from .bookings import _fare
    from .lots import get_scoped_lot

    if user.role != "farmer":
        raise forbid()
    if is_demo_user(user):
        raise HTTPException(403, "Demo accounts can't book real drivers. Sign up with your own farmer account.")
    lot = get_scoped_lot(db, user, lot_id)
    if lot.status != "registered" or lot.shipment_id:
        raise HTTPException(409, "This lot is already in a shipment")
    mandi = db.get(Mandi, body.mandi_id or lot.preferred_mandi_id or -1)
    if mandi is None or mandi.lat is None:
        raise HTTPException(409, "Choose a mandi first")
    expire_due(db)
    if db.scalar(select(TripRequest.id).where(TripRequest.lot_id == lot.id, TripRequest.status == "notified")):
        raise HTTPException(409, "Drivers are already being asked for this lot. Wait for an answer or cancel it.")
    from .auth import known_districts

    want = body.district or mandi.district
    district = next((d for d in known_districts(db) if _norm(d) == _norm(want)), None)
    if district is None:
        raise HTTPException(400, f"Unknown district '{want}'")
    now = _now()
    km = road_km(lot.pickup_lat, lot.pickup_lon, mandi.lat, mandi.lon)[0]
    req = TripRequest(lot_id=lot.id, farmer_id=user.id, district=district, mandi_id=mandi.id,
                      load_tons=lot.quantity_tons, estimated_km=round(km, 1), status="requested", created_at=now)
    db.add(req)
    db.flush()
    _audit(db, req, None, "requested", user.id, district=district, mandi_id=mandi.id)
    lot.preferred_mandi_id = mandi.id
    matches = matching_drivers(db, district, mandi.id, lot.quantity_tons, now)
    if not matches:
        req.reason = f"No drivers are currently available for {mandi.name} ({district})."
        _set_status(db, req, "no_drivers", user.id)
        db.commit()
        return request_out(db, req)
    req.estimated_fare = _fare(db, lot, mandi, matches[0][1].capacity_tons)[0]
    req.notified_at, req.expires_at = now, now + timedelta(seconds=REQUEST_TTL_S)
    for d, v in matches:
        db.add(TripRequestOffer(request_id=req.id, driver_id=d.id, vehicle_id=v.id, status="notified", notified_at=now))
    _set_status(db, req, "notified", user.id, drivers=[d.id for d, _ in matches])
    village = lot.pickup_label or user.district or "the farm"
    for d, _ in matches:
        notify(db, d, "trip_request", f"tripreq:{req.id}", crop=lot.crop, tons=f"{lot.quantity_tons:g}", mandi=mandi.name,
               farmer=user.full_name, village=village, km=f"{km:.0f}")
    db.commit()
    # real-time delivery AFTER the commit, so a driver's phone that reloads on the message sees the request
    for d, _ in matches:
        msg = {"type": "trip_request", "request_id": req.id, **offer_summary(db, req, lot, mandi, user)}
        _publish(f"user:{d.id}", msg)
        sent = webpush.send_to_user(db, d.id, {"title": f"New trip: {lot.crop} {lot.quantity_tons:g} t → {mandi.name}",
                                               "body": f"{user.full_name}, {village} · ~{km:.0f} km. Accept within 5 min.",
                                               "tag": f"tripreq-{req.id}", **msg})
        if sent:
            o = db.scalar(select(TripRequestOffer).where(TripRequestOffer.request_id == req.id,
                                                         TripRequestOffer.driver_id == d.id))
            o.push_sent = sent
    db.commit()
    return request_out(db, req)


@router.get("/lots/{lot_id}/driver-request")
def get_request(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    from .lots import get_scoped_lot

    lot = get_scoped_lot(db, user, lot_id)
    expire_due(db)
    r = latest_request(db, lot.id)
    return request_out(db, r) if r else None


@router.post("/driver-requests/{request_id}/cancel")
def cancel_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    r = db.get(TripRequest, request_id)
    if r is None or r.farmer_id != user.id:
        raise HTTPException(404, "Request not found")
    expire_due(db)
    db.refresh(r)
    if r.status != "notified":
        raise HTTPException(409, f"The request is already {r.status}")
    _set_status(db, r, "cancelled", user.id)
    r.reason = "Cancelled by the farmer."
    _close_offers(db, r, "withdrawn")
    db.commit()
    _broadcast_closed(db, r)
    return request_out(db, r)


# ------------------------------------------------------------------ driver


def offer_summary(db: Session, r: TripRequest, lot: Lot, mandi: Mandi, farmer: User) -> dict:
    from .trips import driver_pay_config

    pay = driver_pay_config()
    km = r.estimated_km or 0
    return {"farmer": farmer.full_name, "village": lot.pickup_label or None, "farmer_district": farmer.district,
            "crop": lot.crop, "grade": lot.grade, "tons": lot.quantity_tons, "pickup_lat": lot.pickup_lat,
            "pickup_lon": lot.pickup_lon, "mandi": mandi.name, "mandi_district": mandi.district, "district": r.district,
            "road_km": km, "fare_estimate": r.estimated_fare,
            "driver_pay_estimate": round(pay["trip_allowance"] + pay["per_km"] * km),
            "expires_at": r.expires_at}


def _offer_out(db: Session, o: TripRequestOffer) -> dict:
    r = db.get(TripRequest, o.request_id)
    lot = db.get(Lot, r.lot_id)
    left = max(0, int((_utc(r.expires_at) - _now()).total_seconds())) if r.expires_at else 0
    return {"request_id": r.id, "offer_status": o.status, "request_status": r.status, "notified_at": o.notified_at,
            "seconds_left": left, **offer_summary(db, r, lot, db.get(Mandi, r.mandi_id), db.get(User, r.farmer_id))}


@router.get("/driver/requests")
def driver_requests(db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """Open trip requests offered to this driver (also the app's check-in: it keeps the driver matched)."""
    expire_due(db)
    a = db.get(DriverAvailability, user.id)
    if a is not None:
        a.last_seen_at = _now()
    offers = db.scalars(select(TripRequestOffer).join(TripRequest, TripRequest.id == TripRequestOffer.request_id)
                        .where(TripRequestOffer.driver_id == user.id, TripRequestOffer.status.in_(OPEN_OFFER),
                               TripRequest.status == "notified").order_by(TripRequestOffer.id.desc())).all()
    now = _now()
    for o in offers:
        if o.seen_at is None:
            o.seen_at, o.status = now, "seen"
    db.commit()
    return [_offer_out(db, o) for o in offers]


@router.post("/driver/heartbeat")
def heartbeat(db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    a = _availability(db, user)
    a.last_seen_at = _now()
    db.commit()
    return {"online": a.online, "matched_now": a.online and is_fresh(db, a)}


def _my_offer(db: Session, user: User, request_id: int) -> tuple[TripRequest, TripRequestOffer]:
    r = db.get(TripRequest, request_id)
    o = db.scalar(select(TripRequestOffer).where(TripRequestOffer.request_id == request_id,
                                                 TripRequestOffer.driver_id == user.id)) if r else None
    if r is None or o is None:
        raise HTTPException(404, "Request not found")
    return r, o


@router.post("/driver/requests/{request_id}/accept")
def accept_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    """First accept wins. The trip is made through the usual make_shipment / make_trip and starts at "accepted"."""
    from .lots import make_shipment
    from .trips import make_trip, trip_out

    expire_due(db)
    r, o = _my_offer(db, user, request_id)
    if r.status != "notified":
        raise HTTPException(409, "Too late: another driver took this trip" if r.status == "accepted"
                            else f"This request is {r.status}")
    if o.status not in OPEN_OFFER:
        raise HTTPException(409, f"You already {o.status} this request")
    if db.scalar(select(Trip.id).where(Trip.driver_id == user.id, Trip.status.in_(ACTIVE_TRIP))):
        raise HTTPException(409, "Finish your current trip first")
    a = db.get(DriverAvailability, user.id)
    v = db.get(Vehicle, (a.vehicle_id if a and a.vehicle_id else None) or o.vehicle_id or -1)
    if v is None or v.is_simulated or v.org_id != user.org_id or v.capacity_tons < r.load_tons:
        raise HTTPException(409, "Your truck can't take this load. Change your truck in Availability.")
    if db.scalar(select(Trip.id).where(Trip.vehicle_id == v.id, Trip.status.in_(ACTIVE_TRIP))):
        raise HTTPException(409, "Your truck is on another trip")
    now = _now()
    claimed = db.execute(update(TripRequest).where(TripRequest.id == r.id, TripRequest.status == "notified")
                         .values(status="accepted", accepted_by=user.id, decided_at=now)
                         .execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        db.rollback()
        raise HTTPException(409, "Too late: another driver took this trip")
    db.refresh(r)
    _audit(db, r, "notified", "accepted", user.id, vehicle_id=v.id)
    lot = db.get(Lot, r.lot_id)
    farmer = db.get(User, r.farmer_id)
    if lot.status != "registered" or lot.shipment_id:  # the farmer booked it another way meanwhile
        _set_status(db, r, "cancelled", None)
        r.reason = "The lot was booked another way."
        _close_offers(db, r, "withdrawn")
        db.commit()
        _broadcast_closed(db, r)
        raise HTTPException(409, "The farmer has already booked this lot another way")
    lot.preferred_mandi_id = r.mandi_id
    sh = make_shipment(db, farmer, r.mandi_id, [lot.id], via="direct_request", request_id=r.id)
    sh.fleet_org_id, sh.booked_at = user.org_id, now
    db.flush()
    db.expire(sh, ["lots"])  # make_trip reads the shipment's lots
    move(db, sh, "booked", user.id, fleet_org_id=user.org_id, via="direct_request", request_id=r.id)
    trip = make_trip(db, user, sh.id, v.id, user.id, channel="direct", via="direct_request", request_id=r.id)
    if trip.is_simulated:  # cannot happen (real truck, real shipment); refuse rather than mislabel a real trip
        raise HTTPException(500, "A direct trip must not be simulated")
    move(db, trip, "accepted", user.id, via="direct_request", request_id=r.id)
    r.trip_id = trip.id
    o.status, o.responded_at = "accepted", now
    o.seen_at = o.seen_at or now
    _close_offers(db, r, "withdrawn", except_driver=user.id)
    notify(db, farmer, "driver_accepted", f"accepted:{r.id}", lot=lot.id, driver=user.full_name,
           vehicle=v.registration, mandi=trip.mandi.name, phone=user.phone or "-")
    db.commit()
    _broadcast_closed(db, r)
    return trip_out(db, trip, user)


@router.post("/driver/requests/{request_id}/decline")
def decline_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(require("trips:drive"))):
    expire_due(db)
    r, o = _my_offer(db, user, request_id)
    if o.status not in OPEN_OFFER:
        raise HTTPException(409, f"You already {o.status} this request")
    o.status, o.responded_at = "declined", _now()
    o.seen_at = o.seen_at or o.responded_at
    db.flush()
    still_open = db.scalar(select(func.count()).select_from(TripRequestOffer).where(
        TripRequestOffer.request_id == r.id, TripRequestOffer.status.in_(OPEN_OFFER)))
    closed = False
    if r.status == "notified" and not still_open:
        _set_status(db, r, "declined", user.id)
        r.reason = "Every driver who was asked said no."
        _tell_farmer_no_driver(db, r)
        closed = True
    db.commit()
    if closed:
        _broadcast_closed(db, r)
    else:
        _publish(f"user:{r.farmer_id}", {"type": "trip_request_update", "request_id": r.id, "lot_id": r.lot_id,
                                         "status": r.status})
    return {"request_id": r.id, "offer_status": o.status, "request_status": r.status}


# ------------------------------------------------------------------ Web Push subscriptions


@router.get("/push/vapid-public-key")
def vapid_public_key(db: Session = Depends(get_db)):
    return {"key": webpush.vapid_keys(db)[1]}


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=2000, pattern="^https://")
    keys: PushKeys


@router.post("/push/subscribe", status_code=201)
def push_subscribe(body: PushSubIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    webpush.subscribe(db, user.id, body.endpoint, body.keys.p256dh, body.keys.auth, request.headers.get("user-agent"))
    a = db.get(DriverAvailability, user.id)
    if a is not None:
        a.last_seen_at = _now()
    db.commit()
    return {"subscribed": True}


class PushUnsubIn(BaseModel):
    endpoint: str = Field(max_length=2000)


@router.post("/push/unsubscribe")
def push_unsubscribe(body: PushUnsubIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..models import PushSubscription

    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == webpush.endpoint_hash(body.endpoint),
                                                   PushSubscription.user_id == user.id))
    if sub is not None:
        db.delete(sub)
        db.commit()
    return {"subscribed": False}


# ------------------------------------------------------------------ admin: which trips came from where


@router.get("/admin/booking-channels")
def booking_channels(db: Session = Depends(get_db), user: User = Depends(require("admin:all"))):
    """Trips by booking channel (direct / farmer -> company / FPO -> fleet / demo autopilot) and real vs simulated,
    plus the latest direct requests with how fast drivers saw and answered them."""
    rows = db.execute(select(Trip.booking_channel, Trip.is_simulated, Trip.status, func.count())
                      .group_by(Trip.booking_channel, Trip.is_simulated, Trip.status)).all()
    channels: dict[str, dict] = {}
    for ch, sim, status, n in rows:
        c = channels.setdefault(ch or "unknown", {"channel": ch or "unknown", "real": 0, "simulated": 0, "active": 0,
                                                  "completed": 0, "other": 0})
        c["simulated" if sim else "real"] += n
        c["active" if status in ACTIVE_TRIP else "completed" if status == "completed" else "other"] += n
    reqs = []
    for r in db.scalars(select(TripRequest).order_by(TripRequest.id.desc()).limit(25)):
        offers = db.scalars(select(TripRequestOffer).where(TripRequestOffer.request_id == r.id)).all()

        def secs(a, b):
            return round((_utc(b) - _utc(a)).total_seconds(), 1) if a and b else None

        first_seen = min((o.seen_at for o in offers if o.seen_at), default=None, key=_utc)
        farmer = db.get(User, r.farmer_id)
        reqs.append({"id": r.id, "lot_id": r.lot_id, "farmer": farmer.full_name if farmer else None,
                     "district": r.district, "mandi": db.get(Mandi, r.mandi_id).name, "status": r.status,
                     "created_at": r.created_at, "drivers_notified": len(offers),
                     "push_sent": sum(o.push_sent or 0 for o in offers),
                     "first_seen_s": secs(r.notified_at, first_seen),
                     "answered_s": secs(r.notified_at, r.decided_at) if r.status in ("accepted", "declined") else None,
                     "trip_id": r.trip_id})
    online = [a for a in db.scalars(select(DriverAvailability).where(DriverAvailability.online.is_(True)))]
    return {"channels": sorted(channels.values(), key=lambda c: c["channel"]), "requests": reqs,
            "drivers_online": sum(1 for a in online if is_fresh(db, a)), "drivers_switched_on": len(online)}
