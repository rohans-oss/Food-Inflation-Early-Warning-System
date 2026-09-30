import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("MLFLOW_DISABLE", "1")  # tests never write to the real mlruns/; test_eval_harness opts back in

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from agripulse_api import db as dbmod
from agripulse_api.db import Base, get_db, make_engine
from agripulse_api.main import app
from agripulse_api.seed import seed_admin, seed_demo, seed_reference

PASSWORD = "agripulse-demo"


@pytest.fixture()
def engine():
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def Session(engine, monkeypatch):
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(dbmod, "SessionLocal", S)
    return S


@pytest.fixture()
def db(Session):
    s = Session()
    seed_reference(s)
    seed_admin(s)
    seed_demo(s)
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(Session, db):
    def _get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login(client, email, password=PASSWORD) -> dict:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def as_role(client):
    cache: dict[str, dict] = {}
    emails = {
        "farmer": "farmer@demo.agripulse",
        "fpo": "fpo@demo.agripulse",
        "driver": "driver@demo.agripulse",
        "fleet_owner": "fleet@demo.agripulse",
        "trader": "trader@demo.agripulse",
        "buyer": "buyer@demo.agripulse",
        "policy": "policy@demo.agripulse",
        "lender": "lender@demo.agripulse",
    }

    def _h(role: str) -> dict:
        if role not in cache:
            cache[role] = login(client, "admin@agripulse.local", "agripulse-admin") if role == "admin" else login(client, emails[role])
        return cache[role]

    return _h
