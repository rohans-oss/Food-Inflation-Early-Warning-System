"""Roles, permissions and tenant scoping.

One permission model for all nine roles (project doc §10: "one permission model").
Tenants (FPOs, fleets, lenders, ...) are Organizations; a user only ever sees rows
belonging to their own org (or to themselves, for farmers and drivers).
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

ROLES: dict[str, tuple[str, str]] = {
    "farmer": ("Farmer", "Registers harvest lots, sees prices, forecasts, best mandi and live vehicle"),
    "fpo": ("FPO / aggregator", "Groups farmer lots into shipments, books vehicles, tracks payouts"),
    "driver": ("Transporter / driver", "Accepts trips, shares GPS during a trip, scans QR at pickup and drop"),
    "fleet_owner": ("Fleet owner", "Manages vehicles and drivers, sees the whole fleet on one map"),
    "trader": ("Mandi trader / commission agent", "Sees incoming supply, confirms arrival, records weight and price"),
    "buyer": ("Bulk buyer", "Plans procurement from forecasts and expected supply"),
    "policy": ("Policy analyst / government", "Monitors price trends, spike risk and supply in transit"),
    "lender": ("Lender / insurer", "Verifies shipment history the farmer has shared with them"),
    "admin": ("Admin / data ops", "Data freshness, failed jobs, users, model performance"),
}

# Which org kind each org-scoped role must belong to.
ROLE_ORG_KIND = {
    "fpo": "fpo",
    "fleet_owner": "fleet",
    "driver": "fleet",
    "lender": "lender",
    "buyer": "buyer",
    "policy": "government",
}

P = {
    "prices:read": {"farmer", "fpo", "trader", "buyer", "policy", "admin", "lender"},
    "forecasts:read": {"farmer", "fpo", "trader", "buyer", "policy", "admin"},
    "recommend:read": {"farmer", "fpo", "admin"},
    "lots:create": {"farmer"},
    "lots:read": {"farmer", "fpo", "trader", "lender", "admin"},
    "shipments:manage": {"fpo", "admin"},
    "members:manage": {"fpo"},  # an FPO adds member farmers and registers lots on their behalf
    "vehicles:manage": {"fleet_owner", "admin"},
    "trips:assign": {"fleet_owner", "fpo", "admin"},
    "trips:drive": {"driver"},
    "trips:read": {"farmer", "fpo", "driver", "fleet_owner", "trader", "lender", "admin"},
    "arrivals:confirm": {"trader"},
    "intransit:read": {"trader", "buyer", "policy", "fpo", "admin"},
    "graph:read": {"fpo", "trader", "buyer", "policy", "admin"},  # V2-3 mandi graph
    "policy:read": {"policy", "admin"},
    "lender:read": {"lender", "admin"},
    "admin:all": {"admin"},
    "alerts:read": set(ROLES),
}

_bearer = HTTPBearer(auto_error=False)


def current_session_id(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str | None:
    """The sid of the request's access token (already validated by get_current_user)."""
    try:
        return decode_token(creds.credentials).get("sid") if creds else None
    except Exception:
        return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    from .sessions import SessionInvalid, active_session

    try:
        active_session(db, payload)  # B-3: a revoked session fails on its very next request
    except SessionInvalid as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


def require(permission: str):
    allowed = P[permission]

    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' lacks '{permission}'")
        return user

    return dep


def has(user: User, permission: str) -> bool:
    return user.role in P[permission]


def forbid() -> HTTPException:
    # 404 rather than 403 for cross-tenant rows, so ids can't be probed.
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
