"""Start the PUBLIC DEMO API (infra/Dockerfile.demo).

Two modes, picked by DATABASE_URL at run time:
- DEMO (default, the SQLite file baked into the image): synthetic, labelled history, a synthetic-trained model, 8 demo
  role logins. Everything resets when the container restarts.
- LIVE (DATABASE_URL=postgresql://..., e.g. a free Neon database; needs DATA_GOV_API_KEY): REAL Agmarknet prices for
  every vegetable, real weather, real history backfill, and a tomato forecast trained on real rows only once the
  readiness monitor allows it (until then there is no forecast, just today's real prices). Demo logins and the
  simulated trucks stay (labelled "Simulated"). Data persists in the database.

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


def prepare_live() -> None:
    """Migrate + seed a persistent database (idempotent), then run the API with the scheduler and start-up catch-up."""
    import secrets
    import subprocess

    env = dict(os.environ)
    env.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(32))  # throwaway unless you set one; prepare_admin decides
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    subprocess.run(["python", "-m", "agripulse_api.seed", "--demo"], check=True, env=env)
    os.environ["ENABLE_SCHEDULER"] = "true"
    os.environ["LIVE_CATCH_UP"] = "true"
    os.environ.setdefault("MODEL_DIR", "/app/live/models")
    if not os.environ.get("DATA_GOV_API_KEY"):
        print("[live] DATA_GOV_API_KEY is not set: no real prices will be fetched", flush=True)


def main():
    os.environ.setdefault("JWT_SECRET", "auto")
    if os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql")):
        print("[live] real-data mode", flush=True)
        prepare_live()
    print("[demo]", prepare_admin(), flush=True)
    port = os.environ.get("PORT", "8000")
    os.execvp("uvicorn", ["uvicorn", "agripulse_api.main:app", "--app-dir", "services/api", "--host", "0.0.0.0",
                          "--port", port, "--proxy-headers", "--forwarded-allow-ips", "*"])


if __name__ == "__main__":
    main()
