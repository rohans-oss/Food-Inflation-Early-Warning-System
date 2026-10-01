from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..lifecycle import history, move
from ..models import AuditLog, Lot, Mandi, Organization, Shipment, Trip, User, Vehicle
from ..rbac import forbid, get_current_user, require
from ..scoping import lot_filter, scoped_lots
from ..supply import forecast_available
from tracking.geo import haversine_km

router = APIRouter(tags=["lots & shipments"])
FLEET_RADIUS_KM = 120  # transporters whose base is farther than this (straight line) from the farm are not offered

# Any vegetable in config/crops.toml can be registered, transported, tracked and paid; price forecasts are tomato only.


class LotIn(BaseModel):
    crop: str = "Tomato"
    quantity_tons: float = Field(gt=0, le=60)
    grade: str = "Local"
    pickup_label: str = ""
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lon: float = Field(ge=-180, le=180)
    fpo_org_id: int | None = None
    lender_org_id: int | None = None


def _trip_for_shipment(db: Session, shipment_id: int | None) -> Trip | None:
    if not shipment_id:
        return None
    return db.scalar(
        select(Trip)
        .where(Trip.shipment_id == shipment_id, Trip.status.not_in(["declined", "cancelled"]))
        .order_by(Trip.id.desc())
    )


def lot_out(db: Session, lot: Lot, viewer: User) -> dict:
    trip = _trip_for_shipment(db, lot.shipment_id)
    out = {
        "id": lot.id,
        "crop": lot.crop,
        "crop_has_forecast": forecast_available(db, lot.crop),
        "quantity_tons": lot.quantity_tons,
        "grade": lot.grade,
        "pickup_label": lot.pickup_label,
        "pickup_lat": lot.pickup_lat,
        "pickup_lon": lot.pickup_lon,
        "status": lot.status,
        "shipment_id": lot.shipment_id,
        "mandi": lot.shipment.mandi.name if lot.shipment else None,
        "mandi_id": lot.shipment.mandi_id if lot.shipment else None,
        "preferred_mandi_id": lot.preferred_mandi_id,
        "preferred_mandi": db.get(Mandi, lot.preferred_mandi_id).name if lot.preferred_mandi_id else None,
        "transport_requested_at": lot.transport_requested_at,
        "receipt_no": lot.receipt_no,
        "receipt_token": lot.receipt_token,
        "payment": {"method": lot.payment_method, "reference": lot.payment_ref, "paid_at": lot.paid_at,
                    "received_at": lot.payment_received_at, "details": lot.payment_details,
                    "amount": round(lot.delivered_weight_kg / 100 * lot.sale_price_per_quintal)
                    if lot.delivered_weight_kg and lot.sale_price_per_quintal else None},
        "delivered_weight_kg": lot.delivered_weight_kg,
        "sale_price_per_quintal": lot.sale_price_per_quintal,
        "payout_status": lot.payout_status,
        "delivered_at": lot.delivered_at,
        "created_at": lot.created_at,
        "is_simulated": lot.is_simulated,
        "farmer": {"id": lot.farmer_id, "name": lot.farmer.full_name},
        "fpo_org_id": lot.org_id,
        "lender_org_id": lot.lender_org_id,
        "trip": None,
    }
    if trip:
        out["trip"] = {
            "id": trip.id,
            "status": trip.status,
            "vehicle": trip.vehicle.registration,
            "eta_at": trip.eta_at,
            "remaining_km": trip.remaining_km,
            "is_simulated": trip.is_simulated,
        }
        # The farmer / FPO get the share link; the pickup QR is shown to them for the driver to scan.
        if viewer.role in ("farmer", "fpo", "admin"):
            s = get_settings()
            if trip.share_token and trip.share_expires_at and trip.share_expires_at > datetime.now(timezone.utc):
                out["trip"]["share_url"] = f"{s.public_base_url}/track/{trip.share_token}"
            if trip.status in ("accepted", "in_progress") and trip.pickup_scanned_at is None:
                out["trip"]["pickup_qr_token"] = trip.pickup_qr_token
    return out


@router.get("/crops")
def list_crops(db: Session = Depends(get_db)):
    """Vegetables a farmer can sell (public): config/crops.toml + ones farmers added. `forecast` = a price forecast is
    available now; `model` = AgriPulse has a model for it (tomato only); `feed_name` = its Agmarknet commodity."""
    from ..crops import all_crops, has_forecast

    return [{**c, "model": has_forecast(c["name"]), "forecast": forecast_available(db, c["name"])} for c in all_crops(db)]


@router.get("/crops/suggestions")
def crop_suggestions(db: Session = Depends(get_db)):
    """Commodity names the live Agmarknet feed actually reported (most rows first), minus crops already listed."""
    from ..crops import all_crops
    from ..models import FeedCommodity

    have = {c["name"].lower() for c in all_crops(db)} | {(c["feed_name"] or "").lower() for c in all_crops(db)}
    rows = db.scalars(select(FeedCommodity).order_by(FeedCommodity.last_rows.desc(), FeedCommodity.name))
    return [{"name": f.name, "last_seen": f.last_seen} for f in rows if f.name.lower() not in have]


class CropIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)


@router.post("/crops", status_code=201)
def add_crop(body: CropIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """A farmer adds a vegetable that is not listed. Matched to the live Agmarknet feed when it has that name (then
    real prices are fetched and kept from now on); otherwise the lot still works, with no price."""
    from ..crops import all_crops, resolve

    name, created = resolve(db, body.name, user_id=user.id, create=True)
    if name is None:
        raise HTTPException(400, "Use letters for the vegetable name (e.g. Drumstick)")
    db.commit()
    crop = next(c for c in all_crops(db) if c["name"] == name)
    if created and crop["feed_name"] and get_settings().data_gov_api_key:
        import threading

        from ingest.agmarknet import fetch_prices_now

        threading.Thread(target=fetch_prices_now, args=(crop["feed_name"],), daemon=True).start()
    return {**crop, "created": created, "prices": "fetching" if created and crop["feed_name"] else
            ("tracked" if crop["feed_name"] else "not in the Agmarknet feed: no price")}


@router.post("/lots", status_code=201)
def create_lot(body: LotIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    from ..crops import resolve

    crop, _ = resolve(db, body.crop, user_id=user.id, create=True)
    if crop is None:
        raise HTTPException(400, "Choose a vegetable, or type its name (letters only)")
    for org_id, kind in ((body.fpo_org_id, "fpo"), (body.lender_org_id, "lender")):
        if org_id is not None:
            org = db.get(Organization, org_id)
            if org is None or org.kind != kind:
                raise HTTPException(400, f"Organization {org_id} is not a {kind}")
    lot = Lot(
        farmer_id=user.id,
        org_id=body.fpo_org_id,
        lender_org_id=body.lender_org_id,
        crop=crop,
        quantity_tons=body.quantity_tons,
        grade=body.grade,
        pickup_label=body.pickup_label,
        pickup_lat=body.pickup_lat,
        pickup_lon=body.pickup_lon,
    )
    db.add(lot)
    db.flush()
    db.add(AuditLog(entity="lot", entity_id=lot.id, from_state=None, to_state="registered", actor_id=user.id,
                    details={"tons": lot.quantity_tons}))
    db.commit()
    db.refresh(lot)
    return lot_out(db, lot, user)


@router.get("/lots")
def list_lots(status: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    q = scoped_lots(user).order_by(Lot.created_at.desc())
    if status:
        q = q.where(Lot.status == status)
    return [lot_out(db, lot, user) for lot in db.scalars(q)]


def get_scoped_lot(db: Session, user: User, lot_id: int) -> Lot:
    lot = db.scalar(select(Lot).where(Lot.id == lot_id, lot_filter(user)))
    if lot is None:
        raise forbid()
    return lot


@router.get("/lots/{lot_id}")
def get_lot(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    return lot_out(db, get_scoped_lot(db, user, lot_id), user)


@router.get("/lots/{lot_id}/history")
def lot_history(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    """Audit trail: every state change of the lot, its shipment and its trip, oldest first."""
    lot = get_scoped_lot(db, user, lot_id)
    rows = [{"entity": "lot", **h} for h in history(db, "lot", lot.id)]
    if lot.shipment_id:
        rows += [{"entity": "shipment", **h} for h in history(db, "shipment", lot.shipment_id)]
        for tid in db.scalars(select(Trip.id).where(Trip.shipment_id == lot.shipment_id)):
            rows += [{"entity": "trip", "trip_id": tid, **h} for h in history(db, "trip", tid)]
    return sorted(rows, key=lambda r: r["at"])


class PreferIn(BaseModel):
    mandi_id: int | None  # None clears the choice


@router.post("/lots/{lot_id}/preferred-mandi")
def choose_mandi(lot_id: int, body: PreferIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """The farmer picks where they want to sell (usually from Best mandi). It is a request: the FPO sees it and
    pre-selects it when grouping; once the lot is grouped the shipment's mandi decides."""
    lot = get_scoped_lot(db, user, lot_id)
    if lot.status != "registered":
        raise HTTPException(409, "The lot is already grouped into a shipment; ask your FPO to change the mandi")
    if body.mandi_id is not None and db.get(Mandi, body.mandi_id) is None:
        raise HTTPException(400, "Unknown mandi")
    before = lot.preferred_mandi_id
    lot.preferred_mandi_id = body.mandi_id
    if before != body.mandi_id:
        lot.transport_requested_at = None  # a new choice needs a new request to the FPO
    name = lambda mid: db.get(Mandi, mid).name[:24] if mid else "none"  # noqa: E731
    db.add(AuditLog(entity="lot", entity_id=lot.id, field="preferred_mandi", from_state=name(before) if before else None,
                    to_state=name(body.mandi_id), actor_id=user.id,
                    details={"preferred_mandi_id": body.mandi_id, "was": before}))
    db.commit()
    return lot_out(db, lot, user)


ACTIVE_TRIP = ("assigned", "accepted", "in_progress")


@router.get("/lots/{lot_id}/next-steps")
def next_steps(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:read"))):
    """After choosing a mandi: the route and cost to it, which fleets have a free truck big enough, and what
    happens next. Truck counts are live from the vehicles / trips tables (simulated vehicles are flagged)."""
    from ..decisions.service import recommend_single

    lot = get_scoped_lot(db, user, lot_id)
    mandi_id = lot.shipment.mandi_id if lot.shipment else lot.preferred_mandi_id
    mandi = db.get(Mandi, mandi_id) if mandi_id else None
    route = None
    if mandi:
        from ..supply import options_without_forecast

        rec = (recommend_single(db, lot.pickup_lat, lot.pickup_lon, lot.quantity_tons, weeks=1) if forecast_available(db, lot.crop)
               else options_without_forecast(db, lot.pickup_lat, lot.pickup_lon, lot.quantity_tons, lot.crop))
        row = next((r for r in rec.get("ranked", []) if r["mandi_id"] == mandi.id), None)
        if row:
            route = {k: row.get(k) for k in ("road_km", "drive_hours", "route_source", "transport_cost", "spoilage_pct",
                                             "net_value", "price_forecast", "data_provenance", "feasible", "why_not",
                                             "price_today", "value_at_today_price")}
            route["vehicle_assumption"] = rec.get("vehicle_assumption")
    from .bookings import booking_out, fleet_availability, open_booking

    fleets = []
    if mandi and lot.status == "registered":
        for org in db.scalars(select(Organization).where(Organization.kind == "fleet").order_by(Organization.name)):
            if org.base_lat is not None and haversine_km(org.base_lat, org.base_lon, lot.pickup_lat, lot.pickup_lon) > FLEET_RADIUS_KM:
                continue  # too far away to come to this farm
            a = fleet_availability(db, org, lot, mandi)
            if a["vehicles"]:
                a["free_slots"] = sum(1 for x in a["slots"] if x["free_trucks"] > 0)
                fleets.append(a)
        # trucks that fit first, then the cheapest fare (it includes the empty run from the fleet's base)
        fleets.sort(key=lambda f: (f["fit"] == 0, f["fare_estimate"] is None, f["fare_estimate"] or 0))
    fpo = db.get(Organization, lot.org_id) if lot.org_id else None
    trip = _trip_for_shipment(db, lot.shipment_id)
    from .direct import expire_due, latest_request, request_out

    expire_due(db)
    dreq = latest_request(db, lot.id)
    booking = open_booking(db, lot.id)
    booking = booking if booking and (booking.shipment_id == lot.shipment_id or booking.status in ("declined", "cancelled")) else None
    done = {
        "registered": True,
        "mandi_chosen": mandi is not None,
        "transport_booked": lot.shipment_id is not None,
        "truck_assigned": trip is not None,
        "picked_up": lot.status in ("in_transit", "at_mandi", "delivered"),
        "at_mandi": lot.status in ("at_mandi", "delivered"),
        "sold": lot.status == "delivered",
        "paid": lot.payout_status == "paid",
    }
    return {"lot_id": lot.id, "mandi": {"id": mandi.id, "name": mandi.name, "district": mandi.district} if mandi else None,
            "mandi_is_final": lot.shipment_id is not None, "route": route, "fleets": fleets,
            "fpo": {"id": fpo.id, "name": fpo.name} if fpo else None,
            "booking": booking_out(db, booking) if booking else None,
            "driver_request": request_out(db, dreq) if dreq else None,
            "via_fpo": lot.shipment_id is not None and (booking is None or booking.shipment_id != lot.shipment_id)
            and not (trip is not None and trip.booking_channel == "direct"),
            "can_request_driver": user.role == "farmer" and lot.status == "registered" and mandi is not None,
            "transport_requested_at": lot.transport_requested_at, "done": done,
            "can_book": user.role == "farmer" and lot.status == "registered" and mandi is not None,
            "can_request": user.role == "farmer" and lot.status == "registered" and mandi is not None and fpo is not None,
            "pickup": _pickup_state(db, lot, trip),
            "demo_mode": get_settings().demo_mode}


def _pickup_state(db: Session, lot: Lot, trip: Trip | None) -> dict | None:
    """What the farmer needs at the farm: the confirmed driver + truck, whether it has arrived, and whether the
    handover (driver's pickup code or QR) is done. In the public demo the demo driver's code is shown here, because
    there is no real driver to tell it."""
    from ..models import GeofenceEvent
    from .bookings import OPEN, open_booking, start_demo

    s = get_settings()
    if s.demo_mode and lot is not None and lot.status in ("grouped", "in_transit", "at_mandi"):
        b = open_booking(db, lot.id)
        if b is not None and b.status in OPEN and b.shipment_id == lot.shipment_id:
            start_demo(lot.id, b.id)  # resumes after a server restart; no-op while running
    if trip is None:
        return None
    driver = db.get(User, trip.driver_id) if trip.driver_id else None
    arrived = db.scalar(select(GeofenceEvent.id).where(GeofenceEvent.trip_id == trip.id,
                                                       GeofenceEvent.event == "reached_pickup")) is not None
    out = {"trip_id": trip.id, "status": trip.status, "driver": driver.full_name.replace(" (driver)", "") if driver else None,
           "driver_phone": getattr(driver, "phone", None) if driver else None,
           "vehicle": trip.vehicle.registration, "capacity_tons": trip.vehicle.capacity_tons,
           "started": trip.status == "in_progress", "arrived": arrived, "picked_up": trip.pickup_scanned_at is not None,
           "code_tries_left": max(0, 5 - (trip.pickup_code_failures or 0))}
    if s.demo_mode and trip.is_simulated and trip.pickup_scanned_at is None:  # never for a real driver's trip
        out["demo_code"] = trip.pickup_code
    return out


@router.post("/lots/{lot_id}/request-transport")
def request_transport(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    """The farmer asks their FPO to ship the lot to the chosen mandi; every FPO desk user gets an alert."""
    from ..alerts import notify

    lot = get_scoped_lot(db, user, lot_id)
    if lot.status != "registered":
        raise HTTPException(409, "This lot is already grouped into a shipment")
    if not lot.preferred_mandi_id:
        raise HTTPException(409, "Choose a mandi first (Sell here)")
    if not lot.org_id:
        raise HTTPException(409, "This lot has no FPO to arrange transport")
    lot.transport_requested_at = datetime.now(timezone.utc)
    mandi = db.get(Mandi, lot.preferred_mandi_id)
    for desk in db.scalars(select(User).where(User.role == "fpo", User.org_id == lot.org_id, User.is_active.is_(True))):
        notify(db, desk, "transport_requested", f"transport:{lot.id}:{lot.preferred_mandi_id}", lot=lot.id,
               farmer=user.full_name, tons=f"{lot.quantity_tons:g}", mandi=mandi.name)
    db.add(AuditLog(entity="lot", entity_id=lot.id, field="transport", from_state=None, to_state="requested",
                    actor_id=user.id, details={"mandi_id": lot.preferred_mandi_id}))
    db.commit()
    return lot_out(db, lot, user)


class ShareIn(BaseModel):
    lender_org_id: int | None  # None revokes


@router.post("/lots/{lot_id}/lender")
def share_with_lender(lot_id: int, body: ShareIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    lot = get_scoped_lot(db, user, lot_id)
    if body.lender_org_id is not None:
        org = db.get(Organization, body.lender_org_id)
        if org is None or org.kind != "lender":
            raise HTTPException(400, "Not a lender organization")
    lot.lender_org_id = body.lender_org_id
    db.commit()
    return lot_out(db, lot, user)


@router.get("/orgs/directory")
def org_directory(kind: str, db: Session = Depends(get_db)):
    """Public, names only: a farmer picks an FPO / lender, an FPO a fleet, a new driver their fleet."""
    if kind not in {"fpo", "fleet", "lender"}:
        raise HTTPException(400, "kind must be fpo, fleet or lender")
    return [{"id": o.id, "name": o.name} for o in db.scalars(select(Organization).where(Organization.kind == kind).order_by(Organization.name))]


# ------------------------------------------------------------------ shipments (FPO)


class ShipmentIn(BaseModel):
    mandi_id: int
    lot_ids: list[int] = Field(min_length=1)


def shipment_out(db: Session, sh: Shipment) -> dict:
    lots = list(sh.lots)
    by_farmer: dict[int, dict] = {}
    for lot in lots:
        f = by_farmer.setdefault(lot.farmer_id, {"farmer_id": lot.farmer_id, "farmer": lot.farmer.full_name,
                                                 "tons": 0.0, "lots": [], "delivered_kg": 0.0, "payout": []})
        f["tons"] = round(f["tons"] + lot.quantity_tons, 3)
        f["lots"].append(lot.id)
        f["delivered_kg"] += lot.delivered_weight_kg or 0
        f["payout"].append(lot.payout_status)
    trip = _trip_for_shipment(db, sh.id)
    fleet = db.get(Organization, sh.fleet_org_id) if sh.fleet_org_id else None
    return {
        "id": sh.id,
        "mandi_id": sh.mandi_id,
        "mandi": sh.mandi.name,
        "status": sh.status,
        "fleet": {"id": fleet.id, "name": fleet.name} if fleet else None,
        "booked_at": sh.booked_at,
        "total_tons": round(sum(lot.quantity_tons for lot in lots), 3),
        "farmers": list(by_farmer.values()),
        "lots": [{"id": lot.id, "farmer": lot.farmer.full_name, "tons": lot.quantity_tons, "status": lot.status,
                  "pickup_lat": lot.pickup_lat, "pickup_lon": lot.pickup_lon, "pickup_label": lot.pickup_label,
                  "delivered_weight_kg": lot.delivered_weight_kg, "sale_price_per_quintal": lot.sale_price_per_quintal,
                  "payout_status": lot.payout_status} for lot in lots],
        "trip": {"id": trip.id, "status": trip.status, "vehicle": trip.vehicle.registration,
                 "pickup_qr_token": trip.pickup_qr_token if trip.pickup_scanned_at is None else None,
                 "eta_at": trip.eta_at, "is_simulated": trip.is_simulated} if trip else None,
        "pickup": _pickup_state(db, None, trip) if trip else None,
        "is_simulated": sh.is_simulated,
        "created_at": sh.created_at,
    }


def _scoped_shipment(db: Session, user: User, shipment_id: int) -> Shipment:
    sh = db.get(Shipment, shipment_id)
    if sh is None:
        raise forbid()
    if user.role == "admin" or (user.role == "fpo" and sh.org_id == user.org_id) or (
        user.role == "fleet_owner" and sh.fleet_org_id == user.org_id
    ):
        return sh
    raise forbid()


def make_shipment(db: Session, user: User, mandi_id: int, lot_ids: list[int], **audit) -> Shipment:
    """Group registered lots into a planned shipment (no commit). Used by POST /shipments and by accepting a V3-1
    shared-load proposal, so both go through the same checks."""
    from ..models import Mandi

    mandi = db.get(Mandi, mandi_id)
    if mandi is None or mandi.lat is None:
        raise HTTPException(400, "Unknown mandi, or its location is not set yet")
    lots = db.scalars(select(Lot).where(Lot.id.in_(lot_ids), lot_filter(user))).all()
    if len(lots) != len(set(lot_ids)):
        raise HTTPException(404, "One or more lots not found in your organization")
    if any(lot.status != "registered" or lot.shipment_id for lot in lots):
        raise HTTPException(409, "Only registered lots that are not already grouped can be added")
    sh = Shipment(org_id=user.org_id, mandi_id=mandi.id, created_by=user.id)
    db.add(sh)
    db.flush()
    db.add(AuditLog(entity="shipment", entity_id=sh.id, from_state=None, to_state="planned", actor_id=user.id,
                    details={"lots": [lot.id for lot in lots], "mandi_id": mandi.id, **audit}))
    for lot in lots:
        lot.shipment_id = sh.id
        move(db, lot, "grouped", user.id, shipment_id=sh.id)
    return sh


@router.post("/shipments", status_code=201)
def create_shipment(body: ShipmentIn, db: Session = Depends(get_db), user: User = Depends(require("shipments:manage"))):
    sh = make_shipment(db, user, body.mandi_id, body.lot_ids)
    db.commit()
    db.refresh(sh)
    return shipment_out(db, sh)


@router.get("/shipments")
def list_shipments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "fpo":
        q = select(Shipment).where(Shipment.org_id == user.org_id)
    elif user.role == "fleet_owner":
        q = select(Shipment).where(Shipment.fleet_org_id == user.org_id)
    elif user.role == "admin":
        q = select(Shipment)
    else:
        raise HTTPException(403, "Not allowed")
    return [shipment_out(db, sh) for sh in db.scalars(q.order_by(Shipment.created_at.desc()))]


@router.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return shipment_out(db, _scoped_shipment(db, user, shipment_id))


class BookIn(BaseModel):
    fleet_org_id: int


@router.post("/shipments/{shipment_id}/book")
def book_vehicle(shipment_id: int, body: BookIn, db: Session = Depends(get_db), user: User = Depends(require("shipments:manage"))):
    sh = _scoped_shipment(db, user, shipment_id)
    fleet = db.get(Organization, body.fleet_org_id)
    if fleet is None or fleet.kind != "fleet":
        raise HTTPException(400, "Not a fleet")
    if db.scalar(select(Trip.id).where(Trip.shipment_id == sh.id, Trip.status.not_in(["declined", "cancelled"]))):
        raise HTTPException(409, "A vehicle is already assigned; it can't be re-booked now")
    move(db, sh, "booked", user.id, fleet_org_id=fleet.id)
    sh.fleet_org_id, sh.booked_at = fleet.id, datetime.now(timezone.utc)
    db.commit()
    if get_settings().demo_mode:  # the demo transporter confirms on its own, like for a farmer's booking
        from .bookings import start_demo_shipment

        start_demo_shipment(sh.id)
    return shipment_out(db, sh)


class HandoverIn(BaseModel):
    code: str = Field(min_length=4, max_length=6)


@router.post("/shipments/{shipment_id}/confirm-pickup")
def confirm_shipment_pickup(shipment_id: int, body: HandoverIn, db: Session = Depends(get_db),
                            user: User = Depends(require("shipments:manage"))):
    """The FPO desk enters the driver's 4-digit pickup code at loading (same check as the farmer's)."""
    from .bookings import check_code_and_pickup

    sh = _scoped_shipment(db, user, shipment_id)
    check_code_and_pickup(db, _trip_for_shipment(db, sh.id), body.code, user.id)
    db.commit()
    if get_settings().demo_mode:  # resume the demo truck after a restart
        from .bookings import start_demo_shipment

        start_demo_shipment(sh.id)
    return shipment_out(db, sh)


@router.post("/lots/{lot_id}/payout")
def mark_paid(lot_id: int, db: Session = Depends(get_db), user: User = Depends(require("shipments:manage"))):
    lot = get_scoped_lot(db, user, lot_id)
    move(db, lot, "paid", user.id, field="payout_status")
    db.commit()
    return lot_out(db, lot, user)


# ------------------------------------------------------------------ fleet (vehicles & drivers)


class VehicleIn(BaseModel):
    registration: str = Field(min_length=4, max_length=20)
    capacity_tons: float = Field(gt=0, le=60)


@router.post("/vehicles", status_code=201)
def add_vehicle(body: VehicleIn, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    reg = body.registration.upper().replace(" ", "-")
    if db.scalar(select(Vehicle).where(Vehicle.registration == reg)):
        raise HTTPException(409, "Vehicle already registered")
    v = Vehicle(org_id=user.org_id, registration=reg, capacity_tons=body.capacity_tons)
    db.add(v)
    db.commit()
    return {"id": v.id, "registration": v.registration, "capacity_tons": v.capacity_tons, "is_simulated": v.is_simulated}


@router.get("/vehicles")
def list_vehicles(db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    q = select(Vehicle) if user.role == "admin" else select(Vehicle).where(Vehicle.org_id == user.org_id)
    return [{"id": v.id, "registration": v.registration, "capacity_tons": v.capacity_tons, "is_simulated": v.is_simulated}
            for v in db.scalars(q.order_by(Vehicle.registration))]


@router.get("/drivers")
def list_drivers(db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    from .auth import INVITE_DOMAIN

    q = select(User).where(User.role == "driver")
    if user.role != "admin":
        q = q.where(User.org_id == user.org_id)
    return [{"id": u.id, "name": u.full_name, "phone": u.phone, "district": u.district, "is_active": u.is_active,
             "invited": u.email.endswith(INVITE_DOMAIN)} for u in db.scalars(q.order_by(User.full_name))]


class DriverInviteIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=20)


@router.post("/drivers", status_code=201)
def invite_driver(body: DriverInviteIn, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    """The fleet owner adds a driver by phone number. Only added drivers can sign up (first sign-in: their vehicle
    number and district); until then the driver cannot log in."""
    import secrets

    from ..security import hash_password
    from .auth import INVITE_DOMAIN, norm_phone

    phone = norm_phone(body.phone)
    if not phone or len(phone) < 10:
        raise HTTPException(400, "Enter a 10-digit mobile number")
    if any(norm_phone(u.phone) == phone for u in db.scalars(select(User).where(User.role == "driver"))):
        raise HTTPException(409, "A driver with this phone number is already added")
    u = User(email=f"driver-{secrets.token_hex(6)}{INVITE_DOMAIN}", full_name=body.name.strip(), phone=phone,
             role="driver", org_id=user.org_id, is_active=False, password_hash=hash_password(secrets.token_urlsafe(24)))
    db.add(u)
    db.flush()
    db.add(AuditLog(entity="user", entity_id=u.id, field="membership", from_state=None, to_state="invited",
                    actor_id=user.id, details={"org_id": user.org_id}))
    db.commit()
    return {"id": u.id, "name": u.full_name, "phone": u.phone, "district": None, "is_active": False, "invited": True}


@router.post("/drivers/{driver_id}/approve")
def approve_driver(driver_id: int, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    d = db.get(User, driver_id)
    if d is None or d.role != "driver" or (user.role != "admin" and d.org_id != user.org_id):
        raise forbid()
    from .auth import INVITE_DOMAIN

    if d.email.endswith(INVITE_DOMAIN):
        raise HTTPException(409, "This driver has not signed up yet; they sign up with the phone number you added")
    d.is_active = True
    db.commit()
    return {"id": d.id, "is_active": True}
