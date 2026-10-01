"""FPO members: an FPO desk adds member farmers (many don't use apps themselves) and registers harvest lots on their
behalf. A member is a farmer account whose org is the FPO. Members added here get no usable password: they can be given
a login later, and until then the FPO acts for them. Every lot created here is audited with the FPO user as the actor."""
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, Lot, User
from ..rbac import require
from ..security import hash_password
from .lots import LotIn, lot_out

router = APIRouter(tags=["fpo members"])


def member_out(db: Session, u: User) -> dict:
    n_lots = db.scalar(select(func.count()).select_from(Lot).where(Lot.farmer_id == u.id, Lot.org_id == u.org_id)) or 0
    waiting = db.scalar(select(func.count()).select_from(Lot).where(Lot.farmer_id == u.id, Lot.org_id == u.org_id,
                                                                   Lot.status == "registered")) or 0
    return {"id": u.id, "name": u.full_name, "phone": u.phone, "lots": n_lots, "waiting": waiting,
            "has_login": not u.email.endswith("@members.agripulse.local")}


def _fpo_members(db: Session, org_id: int):
    """Farmers whose account belongs to this FPO, plus farmers who registered a lot with it."""
    via_lots = select(Lot.farmer_id).where(Lot.org_id == org_id)
    return db.scalars(select(User).where(User.role == "farmer", (User.org_id == org_id) | User.id.in_(via_lots))
                      .order_by(User.full_name)).all()


@router.get("/fpo/members")
def list_members(db: Session = Depends(get_db), user: User = Depends(require("members:manage"))):
    return [member_out(db, u) for u in _fpo_members(db, user.org_id)]


class MemberIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=20)


@router.post("/fpo/members", status_code=201)
def add_member(body: MemberIn, db: Session = Depends(get_db), user: User = Depends(require("members:manage"))):
    phone = re.sub(r"[^\d+]", "", body.phone or "") or None
    if phone and db.scalar(select(User.id).where(User.phone == phone, User.role == "farmer", User.org_id == user.org_id)):
        raise HTTPException(409, "A member with this phone number is already registered")
    u = User(email=f"member-{secrets.token_hex(6)}@members.agripulse.local", full_name=body.name.strip(), phone=phone,
             role="farmer", org_id=user.org_id, password_hash=hash_password(secrets.token_urlsafe(24)))
    db.add(u)
    db.flush()
    db.add(AuditLog(entity="user", entity_id=u.id, field="membership", from_state=None, to_state="member",
                    actor_id=user.id, details={"org_id": user.org_id, "added_by": "fpo"}))
    db.commit()
    return member_out(db, u)


class MemberLotIn(LotIn):
    farmer_id: int


@router.post("/fpo/lots", status_code=201)
def add_member_lot(body: MemberLotIn, db: Session = Depends(get_db), user: User = Depends(require("members:manage"))):
    """Register a harvest for a member farmer. The lot belongs to the farmer and to this FPO."""
    from ..crops import resolve

    farmer = db.get(User, body.farmer_id)
    if farmer is None or farmer.role != "farmer" or farmer not in _fpo_members(db, user.org_id):
        raise HTTPException(404, "Not a member of your FPO")
    crop, _ = resolve(db, body.crop, user_id=user.id, create=True)
    if crop is None:
        raise HTTPException(400, "Choose a vegetable, or type its name (letters only)")
    lot = Lot(farmer_id=farmer.id, org_id=user.org_id, crop=crop, quantity_tons=body.quantity_tons, grade=body.grade,
              pickup_label=body.pickup_label, pickup_lat=body.pickup_lat, pickup_lon=body.pickup_lon)
    db.add(lot)
    db.flush()
    db.add(AuditLog(entity="lot", entity_id=lot.id, from_state=None, to_state="registered", actor_id=user.id,
                    details={"tons": lot.quantity_tons, "registered_by": "fpo", "for_farmer": farmer.id}))
    db.commit()
    db.refresh(lot)
    return lot_out(db, lot, user)
