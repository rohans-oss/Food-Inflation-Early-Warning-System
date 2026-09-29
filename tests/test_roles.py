from datetime import date, timedelta

from sqlalchemy import select

from agripulse_api.alerts import render, spike_alerts
from agripulse_api.models import Alert, Arrival, Forecast, Mandi, User
from agripulse_api.supply import recommend, spoilage_pct
from tests.test_tracking import seed_forecasts


def test_spoilage_rises_with_heat_and_time():
    assert spoilage_pct(2, 30) < spoilage_pct(4, 30)
    assert abs(spoilage_pct(2, 40) / spoilage_pct(2, 30) - 2) < 1e-9  # Q10 = 2


def test_recommender_trades_price_against_distance(db):
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    far = db.scalar(select(Mandi).where(Mandi.name == "Mysuru APMC"))
    today = date.today()
    for m, p50 in ((kolar, 1500), (far, 1600)):
        db.add(Forecast(mandi_id=m.id, commodity="Tomato", issue_date=today, target_date=today + timedelta(weeks=1),
                        horizon_weeks=1, p10=p50 * 0.8, p50=p50, p90=p50 * 1.2, spike_prob=0.1, model_name="lightgbm_quantile"))
    db.commit()
    farm = (13.15, 78.10)  # next to Kolar, ~200 km from Mysuru
    r = recommend(db, *farm, tons=2, radius_km=400)
    names = [x["mandi"] for x in r["ranked"]]
    # Mysuru pays Rs 100/quintal more but the trip eats it: Kolar wins
    assert names.index("Kolar APMC") < names.index("Mysuru APMC")
    top = r["ranked"][0]
    gross = 1500 * 20
    # displayed parts are rounded, so allow a rupee or two
    assert abs(top["net_value"]["p50"] - (gross - top["transport_cost"] - gross * top["spoilage_pct"] / 100)) <= 2
    assert "Bangarpet APMC" in r["no_forecast"]  # nearby but no forecast -> listed, not ranked
    # ranges overlap, so the ranking is flagged as not decisive
    assert top["clearly_better_than_next"] is False


def test_spike_alerts_go_to_the_right_people(db):
    seed_forecasts(db, spike=0.8)
    out = spike_alerts(db)
    assert out["hot_mandis"] > 0 and out["alerts"] > 0
    buyer = db.scalar(select(User).where(User.role == "buyer"))
    got = {a.dedupe_key for a in db.scalars(select(Alert).where(Alert.user_id == buyer.id))}
    assert len(got) == len(buyer.watch_mandi_ids)  # only watched mandis
    trader = db.scalar(select(User).where(User.role == "trader"))
    assert db.scalar(select(Alert).where(Alert.user_id == trader.id)).kind == "price_spike"
    assert spike_alerts(db)["alerts"] == 0  # de-duplicated on the next run
    body = db.scalar(select(Alert).where(Alert.user_id == buyer.id)).body
    assert "[SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT]" in body


def test_kannada_alerts(db, client, as_role):
    title, body = render("delivered", "kn", lot=7, mandi="Kolar APMC", kg=1985, price=1400, payout="pending")
    assert "ಲಾಟ್ #7" in title and "1985" in body
    client.patch("/auth/me", headers=as_role("trader"), json={"preferred_lang": "kn"})
    seed_forecasts(db, spike=0.9)
    spike_alerts(db)
    alerts = client.get("/alerts", headers=as_role("trader")).json()
    assert alerts[0]["lang"] == "kn" and "ಬೆಲೆ" in alerts[0]["title"]
    assert client.post(f"/alerts/{alerts[0]['id']}/read", headers=as_role("trader")).json()["read_at"]
    assert client.post(f"/alerts/{alerts[0]['id']}/read", headers=as_role("farmer")).status_code == 404


def test_buyer_policy_fleet_views(client, as_role, db):
    seed_forecasts(db, spike=0.4)
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    for i in range(10):
        db.add(Arrival(mandi_id=kolar.id, commodity="Tomato", date=date.today() - timedelta(days=i + 1), tonnes=400,
                       source="agmarknet_bulk"))
    db.commit()
    b = client.get("/buyer/overview", headers=as_role("buyer")).json()
    assert len(b["watching"]) == 2 and b["watching"][0]["forecast"]["horizons"][0]["p10"] > 0
    p = client.get("/policy/overview", headers=as_role("policy")).json()
    row = next(m for m in p["mandis"] if m["mandi"] == "Kolar APMC")
    assert row["spike_prob_14d"] == 0.4 and row["arrival_anomaly_7d"] == 0.0
    assert p["districts"][0]["max_spike_prob"] == 0.4
    assert client.get("/policy/overview", headers=as_role("buyer")).status_code == 403
    board = client.get("/supply/in-transit", headers=as_role("trader")).json()[0]
    assert board["typical_daily_tons"] == 400
    f = client.get("/fleet/overview", headers=as_role("fleet_owner")).json()
    assert f["vehicles"][0]["registration"] == "KA-01-XX-1234"
    assert client.post("/vehicles", headers=as_role("fleet_owner"), json={"registration": "ka 05 ab 1111", "capacity_tons": 3}).json()["registration"] == "KA-05-AB-1111"


def test_driver_join_needs_fleet_approval(client, as_role, db):
    fleet_org = client.get("/auth/me", headers=as_role("fleet_owner")).json()["org_id"]
    r = client.post("/auth/register", json={"email": "d2@x.in", "password": "longenough", "full_name": "D2", "role": "driver",
                                              "org_id": fleet_org})
    assert r.status_code == 202
    assert client.post("/auth/login", json={"email": "d2@x.in", "password": "longenough"}).status_code == 403
    d2 = next(d for d in client.get("/drivers", headers=as_role("fleet_owner")).json() if d["name"] == "D2")
    assert client.post(f"/drivers/{d2['id']}/approve", headers=as_role("fleet_owner")).status_code == 200
    assert client.post("/auth/login", json={"email": "d2@x.in", "password": "longenough"}).status_code == 200
