"""Start the PUBLIC DEMO API (infra/Dockerfile.demo). The database was baked into the image at build time: synthetic,
labelled history, a synthetic-trained model, 8 demo role logins. Everything resets when the container restarts.

Admin: enabled only when ADMIN_PASSWORD is set in the host's environment (set it yourself, e.g. in the Render
dashboard); its password is then replaced with that value. Without it the admin account is disabled, so the
throwaway build-time password is never usable.
"""
import os

from sqlalchemy import select


def prepare_admin() -> str:
    from agripulse_api.db import SessionLocal
    from agripulse_api.models import User
    from agripulse_api.security import hash_password

    email = os.environ.get("ADMIN_EMAIL", "admin@agripulse.local")
    password = os.environ.get("ADMIN_PASSWORD", "")
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == email))
        if admin is None:
            return "no admin account in the demo database"
        if password:
            admin.password_hash, admin.is_active = hash_password(password), True
            state = f"admin {email} enabled"
        else:
            admin.is_active = False
            state = "admin disabled (set ADMIN_PASSWORD to enable it)"
        db.commit()
    return state


def main():
    os.environ.setdefault("JWT_SECRET", "auto")
    print("[demo]", prepare_admin(), flush=True)
    port = os.environ.get("PORT", "8000")
    os.execvp("uvicorn", ["uvicorn", "agripulse_api.main:app", "--app-dir", "services/api", "--host", "0.0.0.0",
                          "--port", port, "--proxy-headers", "--forwarded-allow-ips", "*"])


if __name__ == "__main__":
    main()
