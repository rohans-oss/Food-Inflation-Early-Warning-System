"""Farmer-direct transport booking, recorded payments, and the public demo's SIMULATED trip autopilot.

    farmer: choose mandi -> GET /lots/{id}/transport-slots -> POST /lots/{id}/bookings (shipment booked with the fleet)
    fleet owner: assigns truck + driver (POST /trips, the existing flow) -> booking confirmed, farmer alerted
                 or POST /bookings/{id}/decline -> lot back to registered
    driver: the usual trip (accept, consent, start, pickup QR, GPS) -> farmer tracks it live
    trader: delivery QR + weigh -> POST /trader/lots/{id}/payment records how the farmer was paid
    farmer: POST /lots/{id}/payment-received

Payments are RECORDED, never processed: no money moves through AgriPulse.
"""
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..alerts import notify
from ..config import get_settings
from ..db import get_db
from ..lifecycle import move
from ..models import AuditLog, Forecast, Lot, Mandi, Organization, Shipment, TransportBooking, Trip, User, Vehicle
from ..modelcfg import display_model
from ..rbac import forbid, require
from ..supply import cost_config
from .lots import get_scoped_lot, lot_out, make_shipment

router = APIRouter(tags=["bookings & payments"])
log = logging.getLogger("agripulse.bookings")
IST = ZoneInfo("Asia/Kolkata")
SLOT_HOURS = (6, 9, 12, 15, 18)  # pickup slots, IST
SLOT_DAYS = 3
SLOT_WINDOW_H = 3  # a truck booked for a slot is busy for this long either side
OPEN = ("requested", "confirmed")
ACTIVE_TRIP = ("assigned", "accepted", "in_progress")


def _fare(db: Session, lot: Lot, mandi: Mandi, capacity_tons: float, org: Organization | None = None) -> tuple[float, float, str]:
    """(fare estimate Rs, road km farm->mandi, route source). A fleet with a base pays for the whole truck day:
    base -> farm (empty) -> mandi (loaded) -> base; without a base, farm <-> mandi both ways if configured.
    Rate per km depends on the truck size (config/recommender.toml)."""
    from tracking.routing import road_km

    km, _, source = road_km(lot.pickup_lat, lot.pickup_lon, mandi.lat, mandi.lon)
    t = cost_config()["transport"]["vehicle"]
    rate = t["base_rate_per_km"] + t["rate_per_km_per_capacity_ton"] * capacity_tons
    if org is not None and org.base_lat is not None:
        to_farm = road_km(org.base_lat, org.base_lon, lot.pickup_lat, lot.pickup_lon)[0]
        back = road_km(mandi.lat, mandi.lon, org.base_lat, org.base_lon)[0]
        return round((to_farm + km + back) * rate), km, source
    legs = 2 if t.get("count_return_leg", True) else 1
    return round(km * legs * rate), km, source


def _slots(now: datetime) -> list[datetime]:
    local = now.astimezone(IST)
    out = []
    for d in range(SLOT_DAYS):
        day = (local + timedelta(days=d)).date()
        for h in SLOT_HOURS:
            s = datetime(day.year, day.month, day.day, h, tzinfo=IST)
            if s > local + timedelta(hours=1):
                out.append(s.astimezone(timezone.utc))
    return out


def fleet_availability(db: Session, org: Organization, lot: Lot, mandi: Mandi, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    vs = db.scalars(select(Vehicle).where(Vehicle.org_id == org.id)).all()
    fits = [v for v in vs if v.capacity_tons >= lot.quantity_tons]
    busy_now = set(db.scalars(select(Trip.vehicle_id).where(Trip.status.in_(ACTIVE_TRIP), Trip.fleet_org_id == org.id)))
    booked = db.scalars(select(TransportBooking).where(TransportBooking.fleet_org_id == org.id,
                                                       TransportBooking.status.in_(OPEN))).all()
    slots = []
    for s in _slots(now):
        taken = sum(1 for b in booked if abs((_utc(b.pickup_at) - s).total_seconds()) < SLOT_WINDOW_H * 3600)
        on_road = len([v for v in fits if v.id in busy_now]) if s - now < timedelta(hours=6) else 0
        free = len(fits) - taken - on_road
        slots.append({"pickup_at": s, "label": s.astimezone(IST).strftime("%a %d %b, %I:%M %p"), "free_trucks": max(free, 0)})
    cap = min((v.capacity_tons for v in fits), default=None)
    fare, km, source = _fare(db, lot, mandi, cap, org) if cap else (None, None, None)
    from tracking.geo import haversine_km

    drivers = db.scalars(select(User.full_name).where(User.role == "driver", User.org_id == org.id,
                                                      User.is_active.is_(True)).order_by(User.id)).all()
    base_km = (round(haversine_km(org.base_lat, org.base_lon, lot.pickup_lat, lot.pickup_lon) * 1.3)
               if org.base_lat is not None else None)  # road ~ 1.3 x straight line, for display only
    return {"org_id": org.id, "name": org.name, "vehicles": len(vs), "fit": len(fits),
            "base": org.base_label, "base_km_from_farm": base_km, "drivers": [d.replace(" (driver)", "") for d in drivers],
            "capacities_tons": sorted({v.capacity_tons for v in fits}), "is_simulated": bool(vs) and all(v.is_simulated for v in vs),
            "fare_estimate": fare, "road_km": km, "route_source": source, "truck_tons": cap, "slots": slots}


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def booking_out(db: Session, b: TransportBooking) -> dict:
    trip = db.get(Trip, b.trip_id) if b.trip_id else None
    lot = db.get(Lot, b.lot_id)
    return {"id": b.id, "lot_id": b.lot_id, "shipment_id": b.shipment_id, "status": b.status,
            "fleet": db.get(Organization, b.fleet_org_id).name, "fleet_org_id": b.fleet_org_id,
            "mandi": db.get(Mandi, b.mandi_id).name, "mandi_id": b.mandi_id, "farmer": lot.farmer.full_name,
            "tons": lot.quantity_tons, "pickup_label": lot.pickup_label,
            "pickup_at": b.pickup_at, "pickup_local": _utc(b.pickup_at).astimezone(IST).strftime("%a %d %b, %I:%M %p"),
            "fare_estimate": b.fare_estimate, "reason": b.reason, "created_at": b.created_at, "decided_at": b.decided_at,
            "trip": {"id": trip.id, "status": trip.status, "vehicle": trip.vehicle.registration,
                     "driver": db.get(User, trip.driver_id).full_name if trip.driver_id else None, "is_simulated": trip.is_simulated} if trip else None}


def open_booking(db: Session, lot_id: int) -> TransportBooking | None:
    return db.scalar(select(TransportBooking).where(TransportBooking.lot_id == lot_id)
                     .order_by(TransportBooking.id.desc()).limit(1))


# ------------------------------------------------------------------ farmer


@router.get("/lots/{lot_id}/transport-slots")
def transport_slots(lot_id: int, fleet_org_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    lot = get_scoped_lot(db, user, lot_id)
    org = db.get(Organization, fleet_org_id)
    if org is None or org.kind != "fleet":
        raise HTTPException(404, "Transporter not found")
    if not lot.preferred_mandi_id:
        raise HTTPException(409, "Choose a mandi first (Sell here)")
    return fleet_availability(db, org, lot, db.get(Mandi, lot.preferred_mandi_id))


class BookIn(BaseModel):
    fleet_org_id: int
    pickup_at: datetime


@router.post("/lots/{lot_id}/bookings", status_code=201)
def book(lot_id: int, body: BookIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    lot = get_scoped_lot(db, user, lot_id)
    if lot.status != "registered" or lot.shipment_id:
        raise HTTPException(409, "This lot is already in a shipment")
    if not lot.preferred_mandi_id:
        raise HTTPException(409, "Choose a mandi first (Sell here)")
    org = db.get(Organization, body.fleet_org_id)
    if org is None or org.kind != "fleet":
        raise HTTPException(404, "Transporter not found")
    mandi = db.get(Mandi, lot.preferred_mandi_id)
    avail = fleet_availability(db, org, lot, mandi)
    want = _utc(body.pickup_at)
    slot = next((s for s in avail["slots"] if abs((s["pickup_at"] - want).total_seconds()) < 60), None)
    if slot is None:
        raise HTTPException(400, "That pickup time is not one of the offered slots")
    if slot["free_trucks"] < 1:
        raise HTTPException(409, "No truck of this transporter is free for that slot; pick another time")
    sh = make_shipment(db, user, mandi.id, [lot.id], via="farmer_booking")
    sh.fleet_org_id, sh.booked_at = org.id, datetime.now(timezone.utc)
    move(db, sh, "booked", user.id, fleet_org_id=org.id, via="farmer_booking")
    b = TransportBooking(lot_id=lot.id, shipment_id=sh.id, farmer_id=user.id, fleet_org_id=org.id, mandi_id=mandi.id,
                         pickup_at=slot["pickup_at"], fare_estimate=avail["fare_estimate"])
    db.add(b)
    db.flush()
    db.add(AuditLog(entity="lot", entity_id=lot.id, field="booking", from_state=None, to_state="requested", actor_id=user.id,
                    details={"booking_id": b.id, "fleet_org_id": org.id, "pickup_at": slot["pickup_at"].isoformat()}))
    for owner in db.scalars(select(User).where(User.role == "fleet_owner", User.org_id == org.id, User.is_active.is_(True))):
        notify(db, owner, "booking_requested", f"booking:{b.id}", farmer=user.full_name, tons=f"{lot.quantity_tons:g}",
               mandi=mandi.name, time=slot["label"])
    db.commit()
    if get_settings().demo_mode:  # the demo transporter answers on its own (it confirms, then drives to the farm)
        start_demo(lot.id, b.id)
    return booking_out(db, b)


@router.get("/bookings")
def list_bookings(db: Session = Depends(get_db), user: User = Depends(require("trips:read"))):
    q = select(TransportBooking).order_by(TransportBooking.id.desc()).limit(100)
    if user.role == "farmer":
        q = q.where(TransportBooking.farmer_id == user.id)
    elif user.role == "fleet_owner":
        q = q.where(TransportBooking.fleet_org_id == user.org_id)
    elif user.role != "admin":
        raise forbid()
    return [booking_out(db, b) for b in db.scalars(q)]


def _release(db: Session, b: TransportBooking, status: str, actor: User, reason: str | None) -> None:
    """Declined / cancelled before a truck was assigned: the lot goes back to registered, free to rebook."""
    if b.status != "requested":
        raise HTTPException(409, f"The booking is {b.status}")
    if db.scalar(select(Trip.id).where(Trip.shipment_id == b.shipment_id, Trip.status.not_in(["declined", "cancelled"]))):
        raise HTTPException(409, "A truck is already assigned; the fleet owner closes that trip instead")
    b.status, b.reason, b.decided_at = status, reason, datetime.now(timezone.utc)
    lot, sh = db.get(Lot, b.lot_id), db.get(Shipment, b.shipment_id)
    move(db, lot, "registered", actor.id, via=f"booking_{status}", booking_id=b.id)
    lot.shipment_id = None
    move(db, sh, "planned", actor.id, via=f"booking_{status}", booking_id=b.id)
    sh.fleet_org_id = None


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    b = db.get(TransportBooking, booking_id)
    if b is None or b.farmer_id != user.id:
        raise forbid()
    _release(db, b, "cancelled", user, "cancelled by the farmer")
    db.commit()
    return booking_out(db, b)


class DeclineIn(BaseModel):
    reason: str = Field(default="No truck free at that time", max_length=200)


@router.post("/bookings/{booking_id}/decline")
def decline_booking(booking_id: int, body: DeclineIn, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    b = db.get(TransportBooking, booking_id)
    if b is None or (user.role != "admin" and b.fleet_org_id != user.org_id):
        raise forbid()
    _release(db, b, "declined", user, body.reason)
    lot = db.get(Lot, b.lot_id)
    notify(db, lot.farmer, "booking_declined", f"declined:{b.id}", lot=lot.id,
           fleet=db.get(Organization, b.fleet_org_id).name, reason=body.reason)
    db.commit()
    return booking_out(db, b)


def on_trip_assigned(db: Session, shipment: Shipment, trip: Trip) -> None:
    """Called by routers.trips.make_trip: assigning a truck + driver to a farmer-booked shipment confirms the booking."""
    b = db.scalar(select(TransportBooking).where(TransportBooking.shipment_id == shipment.id,
                                                 TransportBooking.status == "requested"))
    if b is None:
        return
    b.status, b.trip_id, b.decided_at = "confirmed", trip.id, datetime.now(timezone.utc)
    lot = db.get(Lot, b.lot_id)
    notify(db, lot.farmer, "booking_confirmed", f"confirmed:{b.id}", lot=lot.id, fleet=db.get(Organization, b.fleet_org_id).name,
           vehicle=trip.vehicle.registration, time=_utc(b.pickup_at).astimezone(IST).strftime("%a %d %b, %I:%M %p"))


# ------------------------------------------------------------------ payment (recorded, not processed)


PAY_METHODS = {"upi": "UPI", "cash": "Cash", "bank": "Bank transfer", "other": "Other"}


class PaymentIn(BaseModel):
    method: str = Field(pattern="^(upi|cash|bank|other)$")
    reference: str = Field(default="", max_length=80)


def _record_payment(db: Session, lot: Lot, actor_id: int | None, method: str, reference: str) -> None:
    if lot.status != "delivered":
        raise HTTPException(409, "Weigh the lot first; payment is recorded for delivered lots")
    move(db, lot, "paid", actor_id, field="payout_status", method=method, reference=reference or None)
    lot.payment_method, lot.payment_ref, lot.paid_at = method, reference or None, datetime.now(timezone.utc)
    amount = round((lot.delivered_weight_kg or 0) / 100 * (lot.sale_price_per_quintal or 0))
    notify(db, lot.farmer, "payment_recorded", f"paid:{lot.id}", lot=lot.id, amount=f"{amount:,}",
           method=PAY_METHODS.get(method.split(" ")[0], method), ref=reference or "-")


@router.post("/trader/lots/{lot_id}/payment")
def trader_payment(lot_id: int, body: PaymentIn, db: Session = Depends(get_db), user: User = Depends(require("arrivals:confirm"))):
    """The trader records how the farmer was paid for a delivered lot at their mandi."""
    lot = get_scoped_lot(db, user, lot_id)
    _record_payment(db, lot, user.id, body.method, body.reference)
    db.commit()
    return lot_out(db, lot, user)


@router.post("/lots/{lot_id}/payment-received")
def payment_received(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    lot = get_scoped_lot(db, user, lot_id)
    if lot.payout_status != "paid":
        raise HTTPException(409, "No payment has been recorded for this lot yet")
    lot.payment_received_at = datetime.now(timezone.utc)
    db.add(AuditLog(entity="lot", entity_id=lot.id, field="payment", from_state="paid", to_state="received", actor_id=user.id))
    db.commit()
    return lot_out(db, lot, user)


# ------------------------------------------------------------------ public demo: SIMULATED trip autopilot


DEMO_TASKS: dict[int, threading.Thread] = {}
DEMO_APPROACH_POINTS = 15  # truck driving from its base to the farm
DEMO_POINTS = 45  # farm -> mandi
DEMO_TICK_S = 2.0
DEMO_CONFIRM_S = 6.0  # the demo transporter "thinks" before confirming
DEMO_WAIT_FOR_CODE_S = 3 * 3600  # the truck waits at the farm until the farmer enters the driver's code


def start_demo(lot_id: int, booking_id: int) -> bool:
    """PUBLIC DEMO ONLY: the demo transporter + driver + trader handle this booking like real ones would, except that
    the farmer still confirms the handover with the driver's pickup code. Returns False if already running."""
    t = DEMO_TASKS.get(lot_id)
    if t is not None and t.is_alive():
        return False
    DEMO_TASKS[lot_id] = threading.Thread(target=_autopilot, args=(lot_id, booking_id), name=f"demo-{lot_id}", daemon=True)
    DEMO_TASKS[lot_id].start()
    return True


@router.post("/lots/{lot_id}/demo-trip", status_code=202)
def demo_trip(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """PUBLIC DEMO ONLY (DEMO_MODE): (re)start the demo transporter for this lot's booking. Real deployments 404."""
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not available")
    lot = get_scoped_lot(db, user, lot_id)
    b = open_booking(db, lot.id)
    if b is None or b.status not in OPEN:
        raise HTTPException(409, "Book a transporter first")
    return {"status": "started" if start_demo(lot_id, b.id) else "running"}


def _autopilot(lot_id: int, booking_id: int) -> None:
    import time

    from .. import db as dbmod

    def run(fn, *a):
        with dbmod.SessionLocal() as db:
            return fn(db, *a)

    try:
        if run(_demo_trip_id, booking_id) is None:
            time.sleep(DEMO_CONFIRM_S)
        trip_id, depot = run(_demo_prepare, lot_id, booking_id)
        if not run(_demo_picked_up, trip_id):
            if not run(_demo_at_farm, trip_id):
                for i in range(1, DEMO_APPROACH_POINTS + 1):
                    run(_demo_approach, trip_id, depot, i / DEMO_APPROACH_POINTS)
                    time.sleep(DEMO_TICK_S)
            waited = 0.0
            while not run(_demo_picked_up, trip_id):  # the farmer enters the driver's code (POST /lots/{id}/confirm-pickup)
                if waited > DEMO_WAIT_FOR_CODE_S:
                    return
                time.sleep(2)
                waited += 2
            time.sleep(2)
        for i in range(1, DEMO_POINTS + 1):
            run(_demo_step, trip_id, i / DEMO_POINTS)
            time.sleep(DEMO_TICK_S)
        time.sleep(3)
        run(_demo_finish, lot_id, trip_id)
    except Exception:  # never take the API down; the visitor sees the trip stop
        log.exception("demo autopilot failed for lot %s", lot_id)


def _demo_trip_id(db: Session, booking_id: int) -> int | None:
    b = db.get(TransportBooking, booking_id)
    return db.scalar(select(Trip.id).where(Trip.shipment_id == b.shipment_id, Trip.status.not_in(["declined", "cancelled"])))


def _demo_picked_up(db: Session, trip_id: int) -> bool:
    return db.get(Trip, trip_id).pickup_scanned_at is not None


def _demo_at_farm(db: Session, trip_id: int) -> bool:
    from ..models import GeofenceEvent

    return db.scalar(select(GeofenceEvent.id).where(GeofenceEvent.trip_id == trip_id,
                                                    GeofenceEvent.event == "reached_pickup")) is not None


def _demo_prepare(db: Session, lot_id: int, booking_id: int) -> tuple[int, tuple[float, float]]:
    """Confirm (simulated truck), accept, consent, start. The truck starts at a depot ~9 km from the farm, on the
    side away from the mandi, so the farmer sees it come to the farm before it heads to the mandi."""
    from .trips import make_trip

    b = db.get(TransportBooking, booking_id)
    lot = db.get(Lot, lot_id)
    now = datetime.now(timezone.utc)
    trip = db.scalar(select(Trip).where(Trip.shipment_id == b.shipment_id, Trip.status.not_in(["declined", "cancelled"])))
    if trip is None:  # the SIMULATED transporter confirms with a SIMULATED truck
        owner = db.scalar(select(User).where(User.role == "fleet_owner", User.org_id == b.fleet_org_id))
        busy = set(db.scalars(select(Trip.driver_id).where(Trip.status.in_(ACTIVE_TRIP))))
        busy_v = set(db.scalars(select(Trip.vehicle_id).where(Trip.status.in_(ACTIVE_TRIP))))
        drivers = db.scalars(select(User).where(User.role == "driver", User.org_id == b.fleet_org_id,
                                                User.is_active.is_(True)).order_by(User.id)).all()
        driver = next((d for d in drivers if d.id not in busy), drivers[0] if drivers else None)
        if owner is None or driver is None:
            raise RuntimeError("demo fleet needs an owner and a driver")
        # the fleet's own SIMULATED truck that fits (smallest first), else a new simulated one
        v = db.scalar(select(Vehicle).where(Vehicle.org_id == b.fleet_org_id, Vehicle.is_simulated.is_(True),
                                            Vehicle.capacity_tons >= lot.quantity_tons, Vehicle.id.not_in(busy_v or {-1}))
                      .order_by(Vehicle.capacity_tons))
        if v is None:
            reg = f"SIM-KA-{secrets.randbelow(90) + 10}-{secrets.randbelow(9000) + 1000}"
            v = Vehicle(org_id=b.fleet_org_id, registration=reg, capacity_tons=max(5.0, lot.quantity_tons), is_simulated=True)
            db.add(v)
            db.flush()
        db.get(Shipment, b.shipment_id).is_simulated = True
        trip = make_trip(db, owner, b.shipment_id, v.id, driver.id, via="demo_autopilot")
    trip.is_simulated = True
    if trip.status == "assigned":
        move(db, trip, "accepted", trip.driver_id, via="demo_autopilot")
    trip.consent_given_at = trip.consent_given_at or now
    if trip.status == "accepted":
        move(db, trip, "in_progress", trip.driver_id, via="demo_autopilot")
        trip.started_at = now
        trip.share_token = trip.share_token or secrets.token_urlsafe(24)
        trip.share_expires_at = now + timedelta(hours=get_settings().share_link_hours)
        if trip.shipment and trip.shipment.status == "booked":
            move(db, trip.shipment, "in_transit", trip.driver_id, trip_id=trip.id, via="demo_autopilot")
    m = trip.mandi
    org = db.get(Organization, b.fleet_org_id)
    if org.base_lat is not None:  # the truck comes from the transporter's own base
        depot = (org.base_lat, org.base_lon)
    else:
        dlat, dlon = trip.origin_lat - m.lat, trip.origin_lon - m.lon
        norm = max((dlat ** 2 + dlon ** 2) ** 0.5, 1e-6)
        depot = (trip.origin_lat + 0.08 * dlat / norm, trip.origin_lon + 0.08 * dlon / norm)
    db.commit()
    return trip.id, depot


def _demo_approach(db: Session, trip_id: int, depot: tuple[float, float], frac: float) -> None:
    from tracking.engine import process_points

    t = db.get(Trip, trip_id)
    if t is None or t.status != "in_progress":
        return
    lat = depot[0] + (t.origin_lat - depot[0]) * frac
    lon = depot[1] + (t.origin_lon - depot[1]) * frac
    process_points(db, t, [{"recorded_at": datetime.now(timezone.utc), "lat": lat, "lon": lon, "speed_kmph": 38,
                            "accuracy_m": 15}])
    db.commit()


def _demo_pickup(db: Session, trip_id: int) -> None:
    """The demo driver scans the farmer's pickup QR (same effects as POST /trips/{id}/scan/pickup)."""
    from .trips import record_pickup

    record_pickup(db, db.get(Trip, trip_id), db.get(Trip, trip_id).driver_id, via="demo_autopilot")
    db.commit()


# ------------------------------------------------------------------ farmer confirms the handover with the driver's code

MAX_CODE_FAILURES = 5


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=6)


@router.post("/lots/{lot_id}/confirm-pickup")
def confirm_pickup(lot_id: int, body: CodeIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """At the farm the driver tells the farmer the trip's 4-digit pickup code; the farmer enters it here. A match proves
    the right truck is at the farm and hands over the load (same effects as the driver scanning the pickup QR)."""
    from .trips import record_pickup

    lot = get_scoped_lot(db, user, lot_id)
    trip = db.scalar(select(Trip).where(Trip.shipment_id == lot.shipment_id, Trip.status.not_in(["declined", "cancelled"]))
                     .order_by(Trip.id.desc())) if lot.shipment_id else None
    if trip is None:
        raise HTTPException(409, "No truck has been assigned yet")
    if trip.pickup_scanned_at is not None:
        return lot_out(db, lot, user)
    if trip.status != "in_progress":
        raise HTTPException(409, "The driver has not started the trip yet")
    if (trip.pickup_code_failures or 0) >= MAX_CODE_FAILURES:
        raise HTTPException(429, "Too many wrong codes. Ask the driver to scan your pickup QR instead.")
    if not trip.pickup_code or not secrets.compare_digest(body.code.strip(), trip.pickup_code):
        trip.pickup_code_failures = (trip.pickup_code_failures or 0) + 1
        db.commit()
        left = MAX_CODE_FAILURES - trip.pickup_code_failures
        raise HTTPException(400, f"That code does not match this truck. {left} tr{'y' if left == 1 else 'ies'} left.")
    record_pickup(db, trip, user.id, via="pickup_code")
    db.commit()
    return lot_out(db, lot, user)


def _demo_step(db: Session, trip_id: int, frac: float) -> None:
    from tracking.engine import process_points
    from tracking.geo import haversine_km
    from tracking.simulator import _point_along

    t = db.get(Trip, trip_id)
    if t is None or t.status != "in_progress":
        return
    route = t.route_geometry or [[t.origin_lon, t.origin_lat], [t.mandi.lon, t.mandi.lat]]
    total = sum(haversine_km(a[1], a[0], b[1], b[0]) for a, b in zip(route, route[1:]))
    lat, lon, _ = _point_along(route, total * frac)
    process_points(db, t, [{"recorded_at": datetime.now(timezone.utc), "lat": lat, "lon": lon, "speed_kmph": 42,
                            "accuracy_m": 15}])
    db.commit()


DEMO_ASSUMED_PRICE = 2000  # Rs/quintal, ONLY when neither a real price nor a forecast exists; recorded as "assumed"


def _demo_price(db: Session, mandi_id: int, crop: str) -> tuple[float, str]:
    """Price the SIMULATED weighing uses: this crop's latest real Agmarknet price at this mandi, else the display
    model's 1-week p50 for this crop, else a stated assumption. The source is written to the audit log."""
    from ..supply import latest_crop_prices

    real = latest_crop_prices(db, crop).get(mandi_id)
    if real:
        return real["modal"], f"agmarknet {real['date']}" if real["data_provenance"] == "real" else "synthetic price"
    f = db.scalar(select(Forecast).where(Forecast.mandi_id == mandi_id, Forecast.model_name == display_model(),
                                         Forecast.commodity == crop, Forecast.horizon_weeks == 1)
                  .order_by(Forecast.issue_date.desc()).limit(1))
    if f:
        return round(f.p50), f"forecast p50 ({f.data_provenance})"
    return DEMO_ASSUMED_PRICE, "assumed demo price (no price reported)"


def _demo_finish(db: Session, lot_id: int, trip_id: int) -> None:
    """The SIMULATED trader scans the delivery QR, weighs at the forecast p50 price, and records a simulated payment."""
    from tracking.engine import add_event

    t = db.get(Trip, trip_id)
    now = datetime.now(timezone.utc)
    if t.delivery_scanned_at is None:
        t.delivery_scanned_at = now
        add_event(db, t, "delivered", now, t.last_lat, t.last_lon, source="demo_autopilot")
    for x in db.scalars(select(Lot).where(Lot.shipment_id == t.shipment_id)):
        if x.status == "in_transit":
            move(db, x, "at_mandi", None, trip_id=t.id, via="demo_autopilot")
        if x.status == "at_mandi":
            price, price_source = _demo_price(db, t.mandi_id, x.crop)
            x.delivered_weight_kg, x.sale_price_per_quintal, x.delivered_at = round(x.quantity_tons * 985), price, now
            move(db, x, "delivered", None, weight_kg=x.delivered_weight_kg, price_per_quintal=price, via="demo_autopilot",
                 price_source=price_source)
            from .receipts import issue

            issue(db, x)
            notify(db, x.farmer, "delivered", f"delivered:{x.id}", lot=x.id, mandi=t.mandi.name,
                   kg=round(x.delivered_weight_kg), price=price, payout="pending")
            _record_payment(db, x, None, "upi (simulated)", f"SIM-{secrets.token_hex(4).upper()}")
    sh = t.shipment
    if sh and sh.status == "in_transit" and all(x.status == "delivered" for x in sh.lots):
        move(db, sh, "delivered", None, via="demo_autopilot")
    if t.status == "in_progress":
        move(db, t, "completed", None, via="demo_autopilot")
        t.ended_at = now
    db.commit()
