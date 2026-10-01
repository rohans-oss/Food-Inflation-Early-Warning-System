import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mandi, Organization, User, Vehicle
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
    district: str | None = None  # where they work; required for every role except farmer / buyer
    vehicle_registration: str | None = None  # drivers: the truck they drive (added to their fleet if new)
    vehicle_capacity_tons: float | None = Field(default=None, gt=0, le=60)


# Who must say where they work at sign-up (user request 2026-10-01). Drivers can only sign up if a fleet owner has
# added their phone number first (see POST /drivers); farmers and buyers sign up freely.
DISTRICT_REQUIRED = {"trader", "driver", "fleet_owner", "fpo", "lender", "policy"}
INVITE_DOMAIN = "@invite.agripulse.local"


def norm_phone(p: str | None) -> str | None:
    digits = re.sub(r"\D", "", p or "")
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def norm_reg(r: str) -> str:
    return re.sub(r"[\s_]+", "-", r.strip().upper())


def known_districts(db: Session) -> list[str]:
    return sorted({d for d in db.scalars(select(Mandi.district).where(Mandi.district.is_not(None))) if d})


@router.get("/districts")
def districts(db: Session = Depends(get_db)):
    """Public: districts that have mandis (sign-up dropdowns)."""
    return known_districts(db)


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
    district: str | None = None
    phone: str | None = None
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
        district=u.district,
        phone=u.phone,
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
    if body.org_id and body.role != "driver":
        # Joining an existing tenant needs an invite from inside it (drivers: the fleet owner adds them).
        raise HTTPException(403, "Ask someone in that organization to add you")
    district = (body.district or "").strip() or None
    if body.role in DISTRICT_REQUIRED:
        if not district:
            raise HTTPException(400, "Choose the district you work in")
        if body.role != "policy" and district not in known_districts(db):
            raise HTTPException(400, f"Unknown district '{district}'")
    if body.role == "driver":
        return _register_driver(body, district, request, db)

    org_id = None
    needed_kind = ROLE_ORG_KIND.get(body.role)
    if needed_kind:
        if body.org_id:
            # Joining an existing tenant needs an invite from inside it (drivers: the fleet owner adds them).
            raise HTTPException(403, "Ask someone in that organization to add you")
        if not body.org_name or not body.org_name.strip():
            raise HTTPException(400, f"Role '{body.role}' needs the organization's name (org_name)")
        org = Organization(name=body.org_name.strip(), kind=needed_kind)
        if body.role == "fleet_owner":  # the district is the fleet's base: its trucks start there
            pts = db.execute(select(Mandi.lat, Mandi.lon).where(Mandi.district == district, Mandi.lat.is_not(None))).all()
            if pts:
                org.base_label = f"{district} (district)"
                org.base_lat = sum(p[0] for p in pts) / len(pts)
                org.base_lon = sum(p[1] for p in pts) / len(pts)
        db.add(org)
        db.flush()
        org_id = org.id

    if body.role == "trader":
        m = db.get(Mandi, body.mandi_id) if body.mandi_id else None
        if m is None:
            raise HTTPException(400, "Mandi managers / traders must pick their mandi (mandi_id)")
        if m.district != district:
            raise HTTPException(400, f"{m.name} is in {m.district}, not {district}")

    user = User(
        email=body.email.lower(), full_name=body.full_name, phone=norm_phone(body.phone) if body.phone else None,
        district=district, password_hash=hash_password(body.password), role=body.role, org_id=org_id,
        mandi_id=body.mandi_id if body.role == "trader" else None, preferred_lang=body.preferred_lang,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return tokens_for(db, user, request)


def _register_driver(body: RegisterIn, district: str, request: Request, db: Session) -> TokenOut:
    """A driver's first sign-in: only phone numbers a fleet owner added (POST /drivers) can sign up. The driver adds
    the truck they drive and the district they join; the invite becomes their account."""
    phone = norm_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter the phone number your fleet owner registered for you")
    invite = next((u for u in db.scalars(select(User).where(User.role == "driver", User.is_active.is_(False)))
                   if u.email.endswith(INVITE_DOMAIN) and norm_phone(u.phone) == phone), None)
    if invite is None:
        raise HTTPException(403, "Only drivers added by a fleet owner can sign up. Ask your fleet owner to add your phone number.")
    if not body.vehicle_registration:
        raise HTTPException(400, "Enter your vehicle number")
    reg = norm_reg(body.vehicle_registration)
    v = db.scalar(select(Vehicle).where(Vehicle.registration == reg))
    if v is not None and v.org_id != invite.org_id:
        raise HTTPException(409, "That vehicle is registered with another fleet")
    if v is None:
        v = Vehicle(org_id=invite.org_id, registration=reg, capacity_tons=body.vehicle_capacity_tons or 5.0)
        db.add(v)
    invite.email, invite.full_name = body.email.lower(), body.full_name or invite.full_name
    invite.password_hash, invite.preferred_lang = hash_password(body.password), body.preferred_lang
    invite.district, invite.phone, invite.is_active = district, phone, True
    db.commit()
    db.refresh(invite)
    return tokens_for(db, invite, request)


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


@router.get("/sessions")
def my_sessions(user: User = Depends(get_current_user), sid: str | None = Depends(current_session_id),
                db: Session = Depends(get_db)):
    """V3-3: where am I signed in (device, when). `current` marks this device."""
    return [{"id": s.id, "created_at": s.created_at, "last_refresh": s.rotated_at, "device": s.user_agent or "unknown",
             "current": s.id == sid} for s in sessions.list_active(db, user.id)]


@router.post("/sessions/{session_id}/revoke")
def revoke_my_session(session_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sign out one of MY devices. Another user's session id gives 404."""
    s = db.get(sessions.UserSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    n = sessions.revoke(db, user.id, actor_id=user.id, reason="signed out from the device list", via="self_device",
                        only=session_id)
    db.commit()
    return {"sessions_revoked": n}


@router.post("/ws-ticket")
def ws_ticket(user: User = Depends(get_current_user), sid: str | None = Depends(current_session_id)):
    """V3-3: a 60-second, single-use ticket for opening a WebSocket (?ticket=...), instead of the access token."""
    return {"ticket": sessions.ws_ticket(user, sid), "expires_in": sessions.WS_TICKET_SECONDS}


@router.post("/handoff-ticket")
def handoff_ticket(user: User = Depends(get_current_user), sid: str | None = Depends(current_session_id)):
    """"Drive in the app": a 2-minute single-use ticket the web page puts in the app link's #fragment."""
    if user.role != "driver":
        raise HTTPException(403, "Only drivers use the driver app")
    return {"ticket": sessions.handoff_ticket(user, sid), "expires_in": sessions.HANDOFF_SECONDS}


class HandoffIn(BaseModel):
    ticket: str = Field(max_length=2000)


@router.post("/handoff", response_model=TokenOut)
def handoff(body: HandoffIn, request: Request, db: Session = Depends(get_db)):
    """The driver app redeems the ticket for its own new session."""
    u = sessions.redeem_handoff(db, body.ticket)
    if u is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This link has expired. Open the app from the website again, or sign in.")
    return tokens_for(db, u, request)


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
