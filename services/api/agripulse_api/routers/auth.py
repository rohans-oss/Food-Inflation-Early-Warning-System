from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mandi, Organization, User
from ..rbac import ROLE_ORG_KIND, ROLES, get_current_user
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    full_name: str
    role: str
    phone: str | None = None
    preferred_lang: str = Field(default="en", pattern="^(en|kn)$")
    org_id: int | None = None  # join an existing org...
    org_name: str | None = None  # ...or create one (first member of an FPO / fleet / ...)
    mandi_id: int | None = None  # traders


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    role_label: str
    org_id: int | None
    org_name: str | None
    mandi_id: int | None
    preferred_lang: str
    watch_mandi_ids: list[int]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=u.role,
        role_label=ROLES[u.role][0],
        org_id=u.org_id,
        org_name=u.org.name if u.org else None,
        mandi_id=u.mandi_id,
        preferred_lang=u.preferred_lang,
        watch_mandi_ids=u.watch_mandi_ids or [],
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, f"Unknown role. Choose one of {sorted(ROLES)}")
    if body.role == "admin":
        raise HTTPException(403, "Admins are created with the seed script, not self-registration")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")

    org_id = None
    needed_kind = ROLE_ORG_KIND.get(body.role)
    if needed_kind:
        if body.org_id:
            org = db.get(Organization, body.org_id)
            if org is None or org.kind != needed_kind:
                raise HTTPException(400, f"Role '{body.role}' must join an organization of kind '{needed_kind}'")
            if body.role != "driver":
                # Joining an existing tenant other than as a driver needs an invite flow (V3).
                raise HTTPException(403, "Ask an admin to add you to an existing organization")
        elif body.org_name:
            org = Organization(name=body.org_name, kind=needed_kind)
            db.add(org)
            db.flush()
        else:
            raise HTTPException(400, f"Role '{body.role}' needs org_name (new) or org_id (existing)")
        org_id = org.id

    if body.role == "trader":
        if not body.mandi_id or db.get(Mandi, body.mandi_id) is None:
            raise HTTPException(400, "Traders must pick their mandi (mandi_id)")

    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        phone=body.phone,
        password_hash=hash_password(body.password),
        role=body.role,
        org_id=org_id,
        mandi_id=body.mandi_id if body.role == "trader" else None,
        preferred_lang=body.preferred_lang,
        # A driver joining an existing fleet waits for the fleet owner's approval.
        is_active=not (body.role == "driver" and body.org_id),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if not user.is_active:
        raise HTTPException(202, "Registered. Your fleet owner must approve you before you can log in.")
    return TokenOut(access_token=create_access_token(user.id, user.role, user.org_id), user=user_out(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    return TokenOut(access_token=create_access_token(user.id, user.role, user.org_id), user=user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user_out(user)


class PrefsIn(BaseModel):
    preferred_lang: str | None = Field(default=None, pattern="^(en|kn)$")
    watch_mandi_ids: list[int] | None = None


@router.patch("/me", response_model=UserOut)
def update_me(body: PrefsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.preferred_lang:
        user.preferred_lang = body.preferred_lang
    if body.watch_mandi_ids is not None:
        user.watch_mandi_ids = body.watch_mandi_ids
    db.commit()
    return user_out(user)


@router.get("/roles")
def roles():
    return [{"name": k, "label": v[0], "description": v[1], "org_kind": ROLE_ORG_KIND.get(k)} for k, v in ROLES.items()]
