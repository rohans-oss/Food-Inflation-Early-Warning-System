from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..lifecycle import history, move
from ..models import AuditLog, Lot, Organization, Shipment, Trip, User, Vehicle
from ..rbac import forbid, get_current_user, require
from ..scoping import lot_filter, scoped_lots

router = APIRouter(tags=["lots & shipments"])

V1_CROPS = {"Tomato"}  # scope: tomato only in V1


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
        "quantity_tons": lot.quantity_tons,
        "grade": lot.grade,
        "pickup_label": lot.pickup_label,
        "pickup_lat": lot.pickup_lat,
        "pickup_lon": lot.pickup_lon,
        "status": lot.status,
        "shipment_id": lot.shipment_id,
        "mandi": lot.shipment.mandi.name if lot.shipment else None,
        "mandi_id": lot.shipment.mandi_id if lot.shipment else None,
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


@router.post("/lots", status_code=201)
def create_lot(body: LotIn, db: Session = Depends(get_db), user: User = Depends(require("lots:create"))):
    crop = body.crop.strip().title()
    if crop not in V1_CROPS:
        raise HTTPException(400, f"V1 supports {sorted(V1_CROPS)} only")
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
    q = select(User).where(User.role == "driver")
    if user.role != "admin":
        q = q.where(User.org_id == user.org_id)
    return [{"id": u.id, "name": u.full_name, "phone": u.phone, "is_active": u.is_active} for u in db.scalars(q)]


@router.post("/drivers/{driver_id}/approve")
def approve_driver(driver_id: int, db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    d = db.get(User, driver_id)
    if d is None or d.role != "driver" or (user.role != "admin" and d.org_id != user.org_id):
        raise forbid()
    d.is_active = True
    db.commit()
    return {"id": d.id, "is_active": True}
