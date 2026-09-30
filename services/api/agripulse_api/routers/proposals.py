"""V3-1: shared truckloads (FPO) and return loads (fleet owner), as proposals a person accepts or rejects."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..decisions import proposals as P
from ..models import LoadProposal, User
from ..rbac import get_current_user, require

router = APIRouter(tags=["V3-1 loads"])


@router.get("/loads/config")
def loads_config(_=Depends(get_current_user)):
    """Which V3-1 features are switched on (pre-registered switch, config/recommender.toml)."""
    return {"consolidation": P.enabled("consolidation"), "return_loads": P.enabled("return_load"),
            "study": "docs/optimizer-results.md"}


class PlanIn(BaseModel):
    weeks: int = Field(default=1, ge=1, le=4)


@router.post("/loads/plan", status_code=201)
def plan_loads(body: PlanIn | None = None, db: Session = Depends(get_db), user: User = Depends(require("shipments:manage"))):
    """FPO: propose shared truckloads for its registered lots (hired trucks), with the saving vs one truck per lot."""
    if user.role != "fpo":
        raise HTTPException(403, "Shared loads are planned by an FPO for its own lots")
    return P.proposal_out(P.plan_fpo_loads(db, user, body.weeks if body else 1))


@router.post("/fleet/return-loads", status_code=201)
def return_loads(db: Session = Depends(get_db), user: User = Depends(require("vehicles:manage"))):
    """Fleet owner: a job for each truck that delivered today, instead of driving home empty."""
    return P.proposal_out(P.fleet_return_loads(db, user))


@router.get("/loads/proposals")
def list_proposals(kind: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role not in ("fpo", "fleet_owner", "admin"):
        raise HTTPException(403, "Not allowed")
    q = select(LoadProposal).order_by(LoadProposal.id.desc()).limit(20)
    if user.role != "admin":
        q = q.where(LoadProposal.org_id == user.org_id)
    if kind:
        q = q.where(LoadProposal.kind == kind)
    return [P.proposal_out(p) for p in db.scalars(q)]


@router.post("/loads/proposals/{pid}/accept")
def accept(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = P.get_own(db, user, pid)
    need = "fpo" if p.kind == "consolidation" else "fleet_owner"
    if user.role != need:
        raise HTTPException(403, f"Only the {need.replace('_', ' ')} can accept this proposal")
    return P.accept(db, user, p)


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/loads/proposals/{pid}/reject")
def reject(pid: int, body: RejectIn | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = P.get_own(db, user, pid)
    if user.role not in ("fpo", "fleet_owner"):
        raise HTTPException(403, "Only the FPO or fleet owner decides")
    return P.reject(db, user, p, body.reason if body else None)
