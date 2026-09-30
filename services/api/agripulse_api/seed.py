"""Seed roles, mandis, an admin, and (with --demo) one user per role.

    python -m agripulse_api.seed            # roles + mandis + admin
    python -m agripulse_api.seed --demo     # + demo tenants and 9 demo logins

Admin credentials come from ADMIN_EMAIL / ADMIN_PASSWORD; demo users share
DEMO_PASSWORD (default 'agripulse-demo'). Idempotent: safe to run repeatedly.
"""
import argparse
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Mandi, Organization, Role, User, Vehicle
from .rbac import ROLES
from .security import hash_password
from .seed_data import KARNATAKA_TOMATO_MANDIS


def seed_reference(db: Session) -> None:
    for name, (label, desc) in ROLES.items():
        if db.get(Role, name) is None:
            db.add(Role(name=name, label=label, description=desc))
    for market, district, lat, lon in KARNATAKA_TOMATO_MANDIS:
        if db.scalar(select(Mandi).where(Mandi.name == market)) is None:
            db.add(Mandi(name=market, district=district, state="Karnataka", lat=lat, lon=lon, aliases=[market]))
    db.flush()


def _org(db: Session, name: str, kind: str) -> Organization:
    org = db.scalar(select(Organization).where(Organization.name == name))
    if org is None:
        org = Organization(name=name, kind=kind)
        db.add(org)
        db.flush()
    return org


def _user(db: Session, email: str, name: str, role: str, password: str, **kw) -> User:
    u = db.scalar(select(User).where(User.email == email))
    if u is None:
        u = User(email=email, full_name=name, role=role, password_hash=hash_password(password), **kw)
        db.add(u)
        db.flush()
    return u


def seed_admin(db: Session) -> User:
    email = os.environ.get("ADMIN_EMAIL", "admin@agripulse.local")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        if not db.get_bind().url.drivername.startswith("sqlite"):
            existing = db.scalar(select(User).where(User.email == email))
            if existing:
                return existing  # already created on an earlier start
            raise SystemExit("Set ADMIN_PASSWORD in .env before the first start against a real database")
        password = "agripulse-admin"  # local SQLite dev only
    return _user(db, email, "Platform Admin", "admin", password)


DEMO_USERS = [
    # email, name, role, org (name, kind) or None, extra
    ("farmer@demo.agripulse", "Farmer", "farmer", None),
    ("fpo@demo.agripulse", "Kolar Tomato FPO desk", "fpo", ("Kolar Tomato Growers FPO (demo)", "fpo")),
    ("driver@demo.agripulse", "Ravi (driver)", "driver", ("Hebbal Haulage (demo)", "fleet")),
    ("fleet@demo.agripulse", "Hebbal Haulage owner", "fleet_owner", ("Hebbal Haulage (demo)", "fleet")),
    ("trader@demo.agripulse", "Kolar commission agent", "trader", None),
    ("buyer@demo.agripulse", "FreshBasket procurement", "buyer", ("FreshBasket Retail (demo)", "buyer")),
    ("policy@demo.agripulse", "State agri-marketing analyst", "policy", ("Agri Marketing Cell (demo)", "government")),
    ("lender@demo.agripulse", "Rural credit officer", "lender", ("Grama Credit Co-op (demo)", "lender")),
]


def seed_demo(db: Session) -> dict[str, User]:
    password = os.environ.get("DEMO_PASSWORD", "agripulse-demo")
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    bengaluru = db.scalar(select(Mandi).where(Mandi.name == "Binny Mill (FF&V) Bengaluru APMC"))
    users: dict[str, User] = {}
    for email, name, role, org_spec in DEMO_USERS:
        kw = {}
        if org_spec:
            kw["org_id"] = _org(db, *org_spec).id
        if role == "trader":
            kw["mandi_id"] = kolar.id
        if role == "buyer":
            kw["watch_mandi_ids"] = [kolar.id, bengaluru.id]
        users[role] = _user(db, email, name, role, password, **kw)
    fleet_org = users["fleet_owner"].org_id
    if db.scalar(select(Vehicle).where(Vehicle.registration == "KA-01-XX-1234")) is None:
        db.add(Vehicle(org_id=fleet_org, registration="KA-01-XX-1234", capacity_tons=5.0))
    return users


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as db:
        seed_reference(db)
        seed_admin(db)
        if args.demo:
            seed_demo(db)
        db.commit()
    print("seeded" + (" (with demo users)" if args.demo else ""))


if __name__ == "__main__":
    main()
