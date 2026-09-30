"""Proof of delivery & sale ("delivery receipt"). Issued when the lot is weighed at the mandi. It gathers the evidence
AgriPulse holds: pickup QR scan, the GPS track, arrival in the mandi geofence, the delivery QR scan at the gate, the
weight and price, and the recorded payment. The farmer shows it to the mandi / commission agent to get paid; anyone
with the link can verify it (GET /public/receipts/{token}), and it exposes nothing beyond what is printed on it."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, GeofenceEvent, GpsPoint, Lot, Organization, TransportBooking, Trip, User
from ..security import new_token

router = APIRouter(tags=["receipts"])
IST = ZoneInfo("Asia/Kolkata")


def issue(db: Session, lot: Lot) -> None:
    """Call right after the lot is weighed (no commit)."""
    if lot.receipt_token:
        return
    now = datetime.now(timezone.utc)
    lot.receipt_token = new_token(24)
    lot.receipt_no = f"AP-{now.astimezone(IST):%Y%m%d}-{lot.id:05d}"


def receipt_url(lot: Lot) -> str | None:
    return f"{get_settings().public_base_url}/receipt/{lot.receipt_token}" if lot.receipt_token else None


def _local(d: datetime | None) -> str | None:
    if d is None:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def build(db: Session, lot: Lot) -> dict:
    sh = lot.shipment
    trip = db.scalar(select(Trip).where(Trip.shipment_id == lot.shipment_id, Trip.status.not_in(["declined", "cancelled"]))
                     .order_by(Trip.id.desc())) if sh else None
    ev = {e.event: e for e in db.scalars(select(GeofenceEvent).where(GeofenceEvent.trip_id == trip.id)
                                         .order_by(GeofenceEvent.occurred_at))} if trip else {}
    n_points = db.scalar(select(func.count()).select_from(GpsPoint).where(GpsPoint.trip_id == trip.id)) if trip else 0
    weigh = db.scalar(select(AuditLog).where(AuditLog.entity == "lot", AuditLog.entity_id == lot.id,
                                            AuditLog.to_state == "delivered").order_by(AuditLog.id.desc()).limit(1))
    trader = db.get(User, weigh.actor_id) if weigh and weigh.actor_id else None
    booking = db.scalar(select(TransportBooking).where(TransportBooking.shipment_id == lot.shipment_id)) if sh else None
    driver = db.get(User, trip.driver_id) if trip and trip.driver_id else None
    amount = round(lot.delivered_weight_kg / 100 * lot.sale_price_per_quintal) if lot.delivered_weight_kg and lot.sale_price_per_quintal else None
    simulated = bool(lot.is_simulated or (trip and trip.is_simulated) or (sh and sh.is_simulated))

    def step(label, at, how, ok=True):
        return {"step": label, "at": at, "at_local": _local(at), "evidence": how, "verified": ok and at is not None}

    timeline = [
        step("Lot registered", lot.created_at, "Farmer's account"),
        step("Transport booked", booking.created_at if booking else (sh.booked_at if sh else None),
             f"Booked with {db.get(Organization, sh.fleet_org_id).name}" if sh and sh.fleet_org_id else "Shipment"),
        step("Truck reached the farm", ev["reached_pickup"].occurred_at if "reached_pickup" in ev else None, "GPS geofence"),
        step("Picked up", trip.pickup_scanned_at if trip else None, "Pickup QR scanned by the driver"),
        step("Reached the mandi", ev["reached_mandi"].occurred_at if "reached_mandi" in ev else None,
             "GPS geofence" if "reached_mandi" in ev and (ev["reached_mandi"].details or {}).get("source") != "qr_scan" else "Delivery QR"),
        step("Delivered at the gate", trip.delivery_scanned_at if trip else None, "Delivery QR scanned at the mandi"),
        step("Weighed and priced", lot.delivered_at, f"Recorded by {trader.full_name}" if trader else "Recorded at the mandi"),
    ]
    return {
        "receipt_no": lot.receipt_no, "issued_at": lot.delivered_at, "issued_local": _local(lot.delivered_at),
        "simulated": simulated,
        "farmer": lot.farmer.full_name, "pickup_label": lot.pickup_label, "crop": lot.crop, "grade": lot.grade,
        "declared_tons": lot.quantity_tons, "lot_id": lot.id,
        "mandi": sh.mandi.name if sh else None, "mandi_district": sh.mandi.district if sh else None,
        "buyer": trader.full_name if trader else ("Simulated trader" if simulated else None),
        "transporter": db.get(Organization, trip.fleet_org_id).name if trip and trip.fleet_org_id else None,
        "vehicle": trip.vehicle.registration if trip else None, "driver": driver.full_name if driver else None,
        "distance_km": round(trip.planned_distance_km, 1) if trip and trip.planned_distance_km else None,
        "gps_points": n_points, "route_source": trip.route_source if trip else None,
        "timeline": timeline,
        "weight_kg": lot.delivered_weight_kg, "price_per_quintal": lot.sale_price_per_quintal, "amount": amount,
        "payment": {"status": lot.payout_status, "method": lot.payment_method, "reference": lot.payment_ref,
                    "paid_at": lot.paid_at, "paid_local": _local(lot.paid_at),
                    "received_local": _local(lot.payment_received_at)},
        "verify_url": receipt_url(lot),
    }


@router.get("/public/receipts/{token}")
def public_receipt(token: str, db: Session = Depends(get_db)):
    lot = db.scalar(select(Lot).where(Lot.receipt_token == token)) if len(token) >= 16 else None
    if lot is None:
        raise HTTPException(404, "No receipt with this code")
    return build(db, lot)
