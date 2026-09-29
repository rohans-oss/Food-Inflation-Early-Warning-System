"""The one place lot / shipment / trip states change.

Every change goes through `move()`, which rejects transitions not in the table below
(HTTP 409) and appends a row to audit_log. Routers never assign `.status` directly.

    lot:      registered -> grouped -> in_transit -> at_mandi -> delivered
    payout:   pending -> paid                     (only once the lot is delivered)
    shipment: planned -> booked -> in_transit -> delivered
    trip:     assigned -> accepted -> in_progress -> completed
              assigned|accepted -> declined ; any open state -> cancelled
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import AuditLog, Lot, Shipment, Trip

TRANSITIONS: dict[tuple[str, str], dict[str, set[str]]] = {
    ("lot", "status"): {
        "registered": {"grouped"},
        "grouped": {"in_transit", "registered"},  # registered = removed from a shipment
        "in_transit": {"at_mandi"},
        "at_mandi": {"delivered"},
        "delivered": set(),
    },
    ("lot", "payout_status"): {"pending": {"paid"}, "paid": set()},
    ("shipment", "status"): {
        "planned": {"booked"},
        "booked": {"booked", "in_transit", "planned"},  # re-book with another fleet; planned = driver declined
        "in_transit": {"delivered"},
        "delivered": set(),
    },
    ("trip", "status"): {
        "assigned": {"accepted", "declined", "cancelled"},
        "accepted": {"in_progress", "declined", "cancelled"},
        "in_progress": {"completed", "cancelled"},
        "completed": set(),
        "declined": set(),
        "cancelled": set(),
    },
}

ENTITY = {Lot: "lot", Shipment: "shipment", Trip: "trip"}


class InvalidTransition(HTTPException):
    def __init__(self, entity: str, field: str, frm: str, to: str):
        super().__init__(409, f"{entity} {field} cannot go from '{frm}' to '{to}'")


def can_move(obj, to: str, field: str = "status") -> bool:
    return to in TRANSITIONS[(ENTITY[type(obj)], field)].get(getattr(obj, field), set())


def move(db: Session, obj, to: str, actor_id: int | None = None, field: str = "status", **details) -> None:
    entity = ENTITY[type(obj)]
    frm = getattr(obj, field)
    if to not in TRANSITIONS[(entity, field)].get(frm, set()):
        raise InvalidTransition(entity, field, frm, to)
    if entity == "lot" and field == "payout_status" and obj.status != "delivered":
        raise HTTPException(409, "Only delivered lots can be marked paid")
    setattr(obj, field, to)
    db.add(AuditLog(entity=entity, entity_id=obj.id, field=field, from_state=frm, to_state=to, actor_id=actor_id,
                    details={k: v for k, v in details.items() if v is not None}))


def history(db: Session, entity: str, entity_id: int) -> list[dict]:
    from sqlalchemy import select

    rows = db.scalars(select(AuditLog).where(AuditLog.entity == entity, AuditLog.entity_id == entity_id)
                      .order_by(AuditLog.id)).all()
    return [{"field": r.field, "from": r.from_state, "to": r.to_state, "actor_id": r.actor_id, "at": r.at,
             "details": r.details} for r in rows]
