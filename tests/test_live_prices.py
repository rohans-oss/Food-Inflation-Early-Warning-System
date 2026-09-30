"""LIVE mode: real Agmarknet prices for every vegetable, farmer-added vegetables, the history probe/backfill, and the
no-forecast fallback (tomato too, until real history passes the readiness threshold)."""
from datetime import date, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from agripulse_api.config import Settings, get_settings
from agripulse_api.models import CustomCrop, DataSourceRun, FeedCommodity, Mandi, Price
from ingest import agmarknet, history
from tests.test_tracking import FARM, seed_forecasts


def _rec(commodity, market="Kolar APMC", state="Karnataka", day=None, modal=1500, district="Kolar"):
    d = (day or date.today()).strftime("%d/%m/%Y")
    return {"state": state, "district": district, "market": market, "commodity": commodity, "variety": "Other",
            "grade": "FAQ", "arrival_date": d, "min_price": modal - 200, "max_price": modal + 200, "modal_price": modal}


@pytest.fixture()
def key(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_gov_api_key", "test-key")
    monkeypatch.setattr(s, "agmarknet_states", "Karnataka")
    monkeypatch.setattr(agmarknet.time, "sleep", lambda s: None)
    monkeypatch.setattr(history.time, "sleep", lambda s: None)
    return s


@respx.mock
def test_daily_pull_fetches_every_commodity_and_stores_the_pickable_ones(db, key):
    recs = [_rec("Tomato"), _rec("Onion", modal=2200), _rec("Bhindi(Ladies Finger)", modal=3000), _rec("Apple", modal=9000)]
    seen = {}

    def api(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"records": recs, "total": len(recs)})

    respx.get(f"{key.agmarknet_base_url}/{key.agmarknet_resource_id}").mock(side_effect=api)
    out = agmarknet.run_daily(db, client=httpx.Client(), raw_dir=None)
    assert "filters[commodity]" not in seen  # one request series for all commodities
    stored = set(db.scalars(select(Price.commodity)))
    assert stored == {"Tomato", "Onion", "Bhindi(ladies finger)"}  # Okra via its Agmarknet name; Apple not stored
    assert out["fetched"] == 4 and out["feed_commodities"] == 4
    assert db.get(FeedCommodity, "Apple").raw_name == "Apple"


def test_real_prices_show_for_any_vegetable_and_rank_by_value_at_that_price(client, as_role, db):
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    agmarknet.store_records(db, [_rec("Onion", modal=2500)])
    agmarknet.store_records(db, [_rec("Onion", market="Old Market", day=date.today() - timedelta(days=40), modal=1)])
    db.commit()
    F = as_role("farmer")
    lot = client.post("/lots", headers=F, json={"crop": "Onion", "quantity_tons": 2, "pickup_lat": FARM[0],
                                                "pickup_lon": FARM[1]}).json()
    rec = client.get("/recommend/best-mandi", headers=F, params={"lot_id": lot["id"]}).json()
    assert rec["no_price_forecast"] and rec["recommender"] == "today_price" and rec["priced_mandis"] == 1
    top = rec["ranked"][0]
    assert top["mandi_id"] == kolar.id and top["price_today"]["modal"] == 2500 and top["price_today"]["source"] == "Agmarknet"
    assert abs(top["value_at_today_price"] - (2500 * 2 * 10 * (1 - top["spoilage_pct"] / 100) - top["transport_cost"])) < 5
    assert top["data_provenance"] == "real"
    assert all(r["price_today"] is None for r in rec["ranked"][1:])  # the 40-day-old row is not "today's price"
    latest = client.get("/prices/latest", headers=F, params={"commodity": "Onion"}).json()
    assert latest[0]["modal_price"] == 2500 and latest[0]["data_provenance"] == "real"


def test_tomato_without_forecasts_falls_back_to_real_prices_and_says_why(client, as_role, db):
    agmarknet.store_records(db, [_rec("Tomato", modal=1800)])
    db.commit()
    F = as_role("farmer")
    lot = client.post("/lots", headers=F, json={"quantity_tons": 1, "pickup_lat": FARM[0], "pickup_lon": FARM[1]}).json()
    assert lot["crop_has_forecast"] is False
    rec = client.get("/recommend/best-mandi", headers=F, params={"lot_id": lot["id"]}).json()
    assert rec["no_price_forecast"] and "real price history" in rec["why_no_forecast"]
    assert rec["ranked"][0]["price_today"]["modal"] == 1800
    seed_forecasts(db)  # once the display model has forecasts, tomato goes back to the forecast recommender
    rec2 = client.get("/recommend/best-mandi", headers=F, params={"lot_id": lot["id"]}).json()
    assert not rec2.get("no_price_forecast") and rec2["ranked"][0]["price_forecast"]["p50"] > 0


def test_farmer_adds_a_vegetable_matched_to_the_feed(client, as_role, db):
    db.add(FeedCommodity(name="Drumstick", raw_name="Drumstick", first_seen=date.today(), last_seen=date.today(), last_rows=4))
    db.commit()
    assert {"name": "Drumstick"} .items() <= client.get("/crops/suggestions").json()[0].items()
    F = as_role("farmer")
    r = client.post("/crops", headers=F, json={"name": "drumstick"}).json()
    assert r["name"] == "Drumstick" and r["feed_name"] == "Drumstick" and r["custom"]
    again = client.post("/crops", headers=F, json={"name": "DRUMSTICK"}).json()
    assert again["created"] is False and db.scalar(select(CustomCrop).where(CustomCrop.name == "Drumstick"))
    from agripulse_api.crops import tracked_feed_names

    assert "Drumstick" in tracked_feed_names(db)  # the daily job keeps its prices from now on
    assert client.get("/crops/suggestions").json() == []
    unknown = client.post("/crops", headers=F, json={"name": "Moon lettuce"}).json()
    assert unknown["feed_name"] is None and "no price" in unknown["prices"]
    assert client.post("/crops", json={"name": "x y"}).status_code == 401


# ------------------------------------------------------------------ history resource (probe first, rule 5)


def _history_api(request):
    """Stand-in for the variety-wise resource: capitalised keys, capitalised filter names, DD/MM/YYYY dates."""
    p = request.url.params
    rows = []
    for back in range(0, 60):
        d = date.today() - timedelta(days=back)
        for st, com in (("Karnataka", "Tomato"), ("Karnataka", "Onion"), ("Tamil Nadu", "Tomato")):
            rows.append({"State": st, "District": "Kolar", "Market": "Kolar APMC", "Commodity": com, "Variety": "Local",
                         "Grade": "FAQ", "Arrival_Date": d.strftime("%d/%m/%Y"), "Min_Price": "1000",
                         "Max_Price": "2000", "Modal_Price": str(1500 + back)})
    if "filters[State]" in p:
        rows = [r for r in rows if r["State"] == p["filters[State]"]]
    if "filters[Commodity]" in p:
        rows = [r for r in rows if r["Commodity"] == p["filters[Commodity]"]]
    if "filters[Arrival_Date]" in p:
        rows = [r for r in rows if r["Arrival_Date"] == p["filters[Arrival_Date]"]]
    off, lim = int(p.get("offset", 0)), min(int(p.get("limit", 10)), 50)  # page cap 50
    return httpx.Response(200, json={"records": rows[off:off + lim], "total": len(rows), "limit": str(lim)})


@respx.mock
def test_history_probe_finds_the_real_filters_then_backfill_uses_them(db, key):
    respx.get(f"{key.agmarknet_base_url}/{key.agmarknet_history_resource_id}").mock(side_effect=_history_api)
    assert history.backfill(db, days=30, veg_days=10)["skipped"].startswith("no usable probe")
    f = history.probe(db, client=httpx.Client())
    assert f["usable"] and f["state_key"] == "State" and f["commodity_key"] == "Commodity"
    assert f["date_key"] == "Arrival_Date" and f["date_format"] == "%d/%m/%Y" and f["page_cap_seen"] == 50
    out = history.backfill(db, days=30, veg_days=10, client=httpx.Client())
    pairs = {(p["state"], p["commodity"]): p for p in out["pairs"]}
    assert pairs[("Karnataka", "Tomato")]["stored"] == 31  # today + 30 days back, older rows skipped
    assert pairs[("Karnataka", "Onion")]["stored"] == 11
    src = set(db.scalars(select(Price.source)))
    assert src == {history.SOURCE}
    # resumable: pairs done to that depth are skipped next time
    assert history.backfill(db, days=30, veg_days=10, client=httpx.Client())["pairs"] == []
    assert db.scalar(select(DataSourceRun).where(DataSourceRun.source == "agmarknet_history_probe")).status == "success"


def test_hosted_postgres_urls_use_the_psycopg3_driver():
    for url in ("postgres://u:p@h/db?sslmode=require", "postgresql://u:p@h/db"):
        assert Settings(database_url=url).database_url.startswith("postgresql+psycopg://u:p@h/db")
    assert Settings(database_url="sqlite://").database_url == "sqlite://"
