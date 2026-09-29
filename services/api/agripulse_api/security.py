"""Password hashing (stdlib PBKDF2) and JWT."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, digest_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
    return hmac.compare_digest(got, expected)


def _encode(user_id: int, typ: str, ttl: timedelta, **claims) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "typ": typ, "iat": now, "exp": now + ttl, "jti": secrets.token_hex(8), **claims}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_access_token(user_id: int, role: str, org_id: int | None) -> str:
    return _encode(user_id, "access", timedelta(minutes=get_settings().jwt_expire_minutes), role=role, org=org_id)


def create_refresh_token(user_id: int) -> str:
    return _encode(user_id, "refresh", timedelta(days=get_settings().jwt_refresh_days))


def decode_token(token: str, typ: str = "access") -> dict:
    """Raises if expired, tampered, or the wrong kind (a refresh token is not an access token)."""
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    if payload.get("typ") != typ:
        raise jwt.InvalidTokenError(f"expected a {typ} token")
    return payload


def new_token(nbytes: int = 24) -> str:
    """Unguessable URL-safe token for share links and QR codes."""
    return secrets.token_urlsafe(nbytes)
