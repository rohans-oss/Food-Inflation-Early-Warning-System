"""Farmer-direct transport booking, recorded payments, and the public demo's SIMULATED trip autopilot.

    farmer: choose mandi -> GET /lots/{id}/transport-slots -> POST /lots/{id}/bookings (shipment booked with the fleet)
    fleet owner: assigns truck + driver (POST /trips, the existing flow) -> booking confirmed, farmer alerted
                 or POST /bookings/{id}/decline -> lot back to registered
    driver: the usual trip (accept, consent, start, pickup QR, GPS) -> farmer tracks it live
    trader: delivery QR + weigh -> POST /trader/lots/{id}/payment records how the farmer was paid
    farmer: POST /lots/{id}/payment-received

Payments are RECORDED, never processed: no money moves through AgriPulse.
"""
import asyncio
import logging
import secrets
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


def _fare(db: Session, lot: Lot, mandi: Mandi, capacity_tons: float) -> tuple[float, float, str]:
    """(fare estimate Rs, road km, route source): road km x rate for that truck size, both ways if configured."""
    from tracking.routing import road_km

    km, _, source = road_km(lot.pickup_lat, lot.pickup_lon, mandi.lat, mandi.lon)
    t = cost_config()["transport"]["vehicle"]
    rate = t["base_rate_per_km"] + t["rate_per_km_per_capacity_ton"] * capacity_tons
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
    fare, km, source = _fare(db, lot, mandi, cap) if cap else (None, None, None)
    return {"org_id": org.id, "name": org.name, "vehicles": len(vs), "fit": len(fits),
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


DEMO_TASKS: dict[int, asyncio.Task] = {}
DEMO_APPROACH_POINTS = 15  # truck driving from its depot to the farm
DEMO_POINTS = 45  # farm -> mandi
DEMO_TICK_S = 2.0  # ~2 min on screen in all


@router.post("/lots/{lot_id}/demo-trip", status_code=202)
async def demo_trip(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """PUBLIC DEMO ONLY (DEMO_MODE): a SIMULATED transporter, driver and trader run this lot's booked trip so a visitor
    can watch it end to end: the truck drives to the farm, the pickup QR is scanned, it drives to the mandi, is scanned
    in at the gate, weighed, and a simulated payment is recorded. Everything is flagged simulated; real deployments 404."""
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not available")
    lot = get_scoped_lot(db, user, lot_id)
    b = open_booking(db, lot.id)
    if b is None or b.status not in OPEN:
        raise HTTPException(409, "Book a transporter first")
    if lot_id in DEMO_TASKS and not DEMO_TASKS[lot_id].done():
        return {"status": "running"}
    DEMO_TASKS[lot_id] = asyncio.get_running_loop().create_task(_autopilot(lot_id, b.id))
    return {"status": "started"}


async def _autopilot(lot_id: int, booking_id: int) -> None:
    from .. import db as dbmod

    def run(fn, *a):
        with dbmod.SessionLocal() as db:
            return fn(db, *a)

    try:
        trip_id, depot = await asyncio.to_thread(run, _demo_prepare, lot_id, booking_id)
        for i in range(1, DEMO_APPROACH_POINTS + 1):
            await asyncio.to_thread(run, _demo_approach, trip_id, depot, i / DEMO_APPROACH_POINTS)
            await asyncio.sleep(DEMO_TICK_S)
        await asyncio.sleep(3)  # loading at the farm
        await asyncio.to_thread(run, _demo_pickup, trip_id)
        for i in range(1, DEMO_POINTS + 1):
            await asyncio.to_thread(run, _demo_step, trip_id, i / DEMO_POINTS)
            await asyncio.sleep(DEMO_TICK_S)
        await asyncio.sleep(3)
        await asyncio.to_thread(run, _demo_finish, lot_id, trip_id)
    except Exception:  # never take the API down; the visitor sees the trip stop
        log.exception("demo autopilot failed for lot %s", lot_id)


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
        driver = db.scalar(select(User).where(User.role == "driver", User.org_id == b.fleet_org_id, User.is_active.is_(True)))
        if owner is None or driver is None:
            raise RuntimeError("demo fleet needs an owner and a driver")
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
    """The SIMULATED driver scans the farmer's pickup QR (same effects as POST /trips/{id}/scan/pickup)."""
    from tracking.engine import add_event

    t = db.get(Trip, trip_id)
    now = datetime.now(timezone.utc)
    if t.pickup_scanned_at is None:
        t.pickup_scanned_at = now
        add_event(db, t, "picked_up", now, t.last_lat or t.origin_lat, t.last_lon or t.origin_lon, source="demo_autopilot")
        link = f"{get_settings().public_base_url}/track/{t.share_token}"
        for x in db.scalars(select(Lot).where(Lot.shipment_id == t.shipment_id)):
            if x.status == "grouped":
                move(db, x, "in_transit", t.driver_id, trip_id=t.id, via="demo_autopilot")
                notify(db, x.farmer, "picked_up", f"pickup:{t.id}:{x.id}", lot=x.id, vehicle=t.vehicle.registration,
                       mandi=t.mandi.name, link=link)
    db.commit()


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
