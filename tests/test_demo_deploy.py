"""Public demo deployment: JWT_SECRET=auto, the default secret refused on a public https address, admin disabled
unless ADMIN_PASSWORD is set."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select

from agripulse_api.config import Settings
from agripulse_api.models import User
from agripulse_api.security import verify_password

ROOT = Path(__file__).resolve().parents[1]


def test_auto_jwt_secret_is_random_per_process():
    a, b = Settings(jwt_secret="auto"), Settings(jwt_secret="auto")
    assert a.jwt_secret != "auto" and len(a.jwt_secret) >= 48 and a.jwt_secret != b.jwt_secret


def test_default_secret_refused_on_a_public_address(monkeypatch):
    import asyncio

    from agripulse_api import main
    from agripulse_api.config import get_settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example.org")
    monkeypatch.setenv("JWT_SECRET", "change-me-in-.env")
    get_settings.cache_clear()
    try:
        async def boot():
            async with main.lifespan(main.app):
                pass
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            asyncio.run(boot())
    finally:
        get_settings.cache_clear()


def _demo_start():
    spec = importlib.util.spec_from_file_location("demo_start", ROOT / "scripts" / "demo_start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_admin_disabled_without_password_and_enabled_with_one(db, monkeypatch):
    import agripulse_api.db as dbmod

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _Ctx(db))
    mod = _demo_start()
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert "disabled" in mod.prepare_admin()
    admin = db.scalar(select(User).where(User.role == "admin"))
    assert admin.is_active is False
    monkeypatch.setenv("ADMIN_PASSWORD", "a-long-owner-chosen-password")
    assert "enabled" in mod.prepare_admin()
    db.refresh(admin)
    assert admin.is_active and verify_password("a-long-owner-chosen-password", admin.password_hash)


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False
