from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mandi, Organization, User
from .. import sessions
from ..rbac import ROLE_ORG_KIND, ROLES, current_session_id, get_current_user
from ..security import decode_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    full_name: str
    role: str
    phone: str | None = None
    preferred_lang: str = Field(default="en", pattern="^(en|kn|hi)$")
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
    is_active: bool = True


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


def tokens_for(db: Session, u: User, request: Request | None = None) -> TokenOut:
    """A new sign-in: a new server-side session (B-3), so it can be revoked later."""
    access, refresh_ = sessions.start(db, u, request.headers.get("user-agent", "") if request else "")
    db.commit()
    return TokenOut(access_token=access, refresh_token=refresh_, user=user_out(u))


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
        is_active=u.is_active,
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
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
    return tokens_for(db, user, request)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    return tokens_for(db, user, request)


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    """Swap a refresh token for a new pair (the old refresh token is retired). Disabled users, role changes and
    revoked sessions take effect here. A retired refresh token presented again revokes its session (theft)."""
    try:
        payload = decode_token(body.refresh_token, typ="refresh")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    try:
        sess = sessions.rotate(db, payload)
    except sessions.SessionInvalid as exc:
        db.commit()  # keep the reuse-detection revocation + audit row
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    access, refresh_ = sessions.issue(user, sess)
    db.commit()
    return TokenOut(access_token=access, refresh_token=refresh_, user=user_out(user))


@router.post("/logout")
def logout(user: User = Depends(get_current_user), sid: str | None = Depends(current_session_id),
           db: Session = Depends(get_db)):
    """End THIS device's session on the server, so a copied token stops working too."""
    n = sessions.revoke(db, user.id, actor_id=user.id, reason="signed out", via="logout", only=sid) if sid else 0
    db.commit()
    return {"sessions_revoked": n}


class LogoutAllIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/logout-all")
def logout_all(body: LogoutAllIn | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Self-service "log out of all devices" (e.g. the account may be compromised). Includes this device."""
    n = sessions.revoke(db, user.id, actor_id=user.id, reason=(body.reason if body else None) or "log out of all devices",
                        via="self_service")
    db.commit()
    return {"sessions_revoked": n}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user_out(user)


class PrefsIn(BaseModel):
    preferred_lang: str | None = Field(default=None, pattern="^(en|kn|hi)$")
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
