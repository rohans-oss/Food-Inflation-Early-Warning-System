from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from agripulse_api.models import Mandi, Price, Trip, Vehicle
from agripulse_api.readiness import compute, provenance_for
from agripulse_api.security import new_token

TODAY = date(2026, 9, 29)


def _prices(db, mandi, start, days, source="agmarknet", skip_every=0):
    for i in range(days):
        if skip_every and i % skip_every == 0:
            continue
        db.add(Price(mandi_id=mandi.id, commodity="Tomato", variety="Local", grade="FAQ", date=start + timedelta(days=i),
                     modal_price=1500, source=source))
    db.commit()


def _row(rep, mandi):
    return next(m for m in rep["mandis"] if m["mandi_id"] == mandi.id)


def _mandi(db, name):
    return db.scalar(select(Mandi).where(Mandi.name == name))


def test_synthetic_rows_never_count(db):
    kolar = _mandi(db, "Kolar APMC")
    _prices(db, kolar, TODAY - timedelta(days=500), 500, source="synthetic")
    r = _row(compute(db, TODAY), kolar)["prices"]
    assert r["status"] == "no_data" and not r["ready"] and r["history_days"] == 0


def test_short_real_history_projects_ready_date(db):
    kolar = _mandi(db, "Kolar APMC")
    start = date(2026, 9, 25)  # real collection started here
    _prices(db, kolar, start, 5)
    r = _row(compute(db, TODAY), kolar)["prices"]
    assert r["status"] == "collecting" and not r["ready"]
    assert r["history_days"] == 5 and r["days_to_go"] == 360
    assert r["projected_ready_date"] == start + timedelta(days=364)  # 2027-09-24


def test_ready_after_a_year_with_normal_gaps(db):
    kolar = _mandi(db, "Kolar APMC")
    _prices(db, kolar, TODAY - timedelta(days=399), 400, skip_every=7)  # Sunday-style closures: ~14% missing
    r = _row(compute(db, TODAY), kolar)["prices"]
    assert r["ready"] and r["status"] == "ready" and 13 < r["missing_pct"] < 16


def test_too_patchy_is_not_ready(db):
    kolar = _mandi(db, "Kolar APMC")
    _prices(db, kolar, TODAY - timedelta(days=399), 400, skip_every=2)  # 50% missing > 30% threshold
    r = _row(compute(db, TODAY), kolar)["prices"]
    assert not r["ready"] and r["status"] == "too_many_gaps"


def test_stalled_collection_has_no_projection(db):
    kolar = _mandi(db, "Kolar APMC")
    _prices(db, kolar, TODAY - timedelta(days=60), 30)  # stopped 30 days ago
    r = _row(compute(db, TODAY), kolar)["prices"]
    assert r["status"] == "stalled" and r["projected_ready_date"] is None


def test_transit_counts_only_real_completed_trips(db):
    kolar = _mandi(db, "Kolar APMC")
    v = db.scalar(select(Vehicle))
    now = datetime.now(timezone.utc)
    for sim in (False, True, True):
        db.add(Trip(vehicle_id=v.id, mandi_id=kolar.id, origin_lat=13.2, origin_lon=78.0, status="completed",
                    is_simulated=sim, started_at=now, pickup_qr_token=new_token(), delivery_qr_token=new_token()))
    db.commit()
    t = _row(compute(db, TODAY), kolar)["transit"]
    assert t["real_trips"] == 1 and not t["ready"] and t["trips_to_go"] == 49


def test_endpoint_and_summary(db, client, as_role):
    _prices(db, _mandi(db, "Kolar APMC"), date(2026, 9, 25), 5)
    rep = client.get("/admin/data-readiness", headers=as_role("admin")).json()
    assert rep["thresholds"]["prices"]["min_real_days"] == 365
    assert rep["summary"]["prices"]["ready"] == 0 and rep["summary"]["prices"]["latest_projected_ready_date"]
    assert client.get("/admin/data-readiness", headers=as_role("farmer")).status_code == 403


def test_provenance_for():
    assert provenance_for(True, True) == "synthetic"
    assert provenance_for(False, False) == "real_partial"
    assert provenance_for(False, True) == "real"
