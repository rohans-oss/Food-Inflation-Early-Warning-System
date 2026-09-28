"""Row-level tenant scoping for lots and trips. Every router goes through these."""
from sqlalchemy import Select, exists, false, select, true

from .models import Lot, Shipment, Trip, User


def lot_filter(user: User):
    r = user.role
    if r == "admin":
        return true()
    if r == "farmer":
        return Lot.farmer_id == user.id
    if r == "fpo":
        return Lot.org_id == user.org_id if user.org_id else false()
    if r == "lender":
        return Lot.lender_org_id == user.org_id if user.org_id else false()
    if r == "trader":
        if not user.mandi_id:
            return false()
        return Lot.shipment_id.in_(select(Shipment.id).where(Shipment.mandi_id == user.mandi_id))
    return false()


def scoped_lots(user: User) -> Select:
    return select(Lot).where(lot_filter(user))


def trip_filter(user: User):
    r = user.role
    if r == "admin":
        return true()
    if r == "driver":
        return Trip.driver_id == user.id
    if r == "fleet_owner":
        return Trip.fleet_org_id == user.org_id if user.org_id else false()
    if r == "trader":
        return Trip.mandi_id == user.mandi_id if user.mandi_id else false()
    if r in ("farmer", "fpo", "lender"):
        # A trip is visible if it carries at least one lot the user can see.
        return exists(
            select(Lot.id).where(Lot.shipment_id == Trip.shipment_id, Lot.shipment_id.is_not(None), lot_filter(user))
        )
    return false()


def scoped_trips(user: User) -> Select:
    return select(Trip).where(trip_filter(user))
