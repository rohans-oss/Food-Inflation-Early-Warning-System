import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from agripulse_api.config import get_settings
from agripulse_api.models import DataSourceRun, Mandi, Price, Weather
from ingest import agmarknet, weather
from ingest.cleaning import clean_record, history_jump_flag, norm_key, parse_date

FIX = Path(__file__).parent / "fixtures" / "agmarknet_ka_tomato_2026-09-25.json"
REAL = json.loads(FIX.read_text())


def test_parse_real_dates_and_names():
    assert parse_date("25/09/2026") == date(2026, 9, 25)
    assert norm_key("Binny Mill (FF&V) Bengaluru APMC") == norm_key("binny mill (FF and V)  Bengaluru")


def test_real_anomaly_is_flagged():
    belgaum = next(r for r in REAL["records"] if r["market"] == "Belgaum APMC")  # min 2500 > max 2000 in the real feed
    row = clean_record(belgaum)
    assert "min_gt_max" in row.flags and "modal_outside" in row.flags and row.is_outlier
    ok = clean_record(next(r for r in REAL["records"] if r["market"] == "Mysuru APMC"))
    assert ok.flags == [] and ok.modal_price == 2500


def test_history_jump():
    assert not history_jump_flag(1500, [1400, 1500, 1450])  # too little history
    assert history_jump_flag(9000, [1400, 1500, 1450, 1520, 1480, 1390, 1510, 1470])
    # a genuine tomato spike (x2.5) must NOT be thrown away as an outlier
    assert not history_jump_flag(3700, [1400, 1500, 1450, 1520, 1480, 1390, 1510, 1470])
    assert history_jump_flag(14.5, [1400, 1500, 1450, 1520, 1480, 1390, 1510, 1470])  # Rs/kg typo


def test_store_real_records_maps_known_and_creates_unknown(db):
    counts = agmarknet.store_records(db, REAL["records"])
    db.commit()
    assert counts["stored"] == 18 and counts["outliers"] == 1
    # Santhesargur isn't seeded (location unconfirmed) -> auto-created, coordinates left empty
    assert counts["new_mandis"] == ["Santhesargur APMC"]
    kolar_like = db.scalar(select(Mandi).where(Mandi.name == "Chintamani APMC"))
    p = db.scalar(select(Price).where(Price.mandi_id == kolar_like.id))
    assert p.modal_price == 1000 and p.date == date(2026, 9, 25)
    # re-ingesting the same day updates instead of duplicating
    again = agmarknet.store_records(db, REAL["records"])
    assert again["stored"] == 0 and again["updated"] == 18


def test_unknown_market_is_created_without_coords(db):
    rec = dict(REAL["records"][0], market="Hosur Uzhavar Sandhai", district="Krishnagiri", state="Tamil Nadu")
    counts = agmarknet.store_records(db, [rec])
    assert counts["new_mandis"] == ["Hosur Uzhavar Sandhai"]
    m = db.scalar(select(Mandi).where(Mandi.name == "Hosur Uzhavar Sandhai"))
    assert m.lat is None and not m.coords_verified


@respx.mock
def test_run_daily_pages_and_logs_run(db, monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "data_gov_api_key", "test-key")
    monkeypatch.setattr(s, "agmarknet_states", "Karnataka,Kerala")
    url = f"{s.agmarknet_base_url}/{s.agmarknet_resource_id}"
    recs = REAL["records"]

    def responder(request):
        state = request.url.params["filters[state]"]
        if state == "Kerala":
            return httpx.Response(500)
        off = int(request.url.params["offset"])
        # the real API caps page size; simulate 10-row pages
        return httpx.Response(200, json={"records": recs[off : off + 10], "total": len(recs)})

    respx.get(url).mock(side_effect=responder)
    monkeypatch.setattr(agmarknet.time, "sleep", lambda s: None)
    out = agmarknet.run_daily(db, client=httpx.Client(), raw_dir=str(tmp_path))
    assert out["per_state"] == {"Karnataka": 18}
    assert "Kerala" in out["state_failures"]
    run = db.scalar(select(DataSourceRun).where(DataSourceRun.source == "agmarknet"))
    assert run.status == "success" and run.rows == 18
    assert (tmp_path / "25-09-2026.json").exists()


def test_run_daily_without_key_fails_and_is_logged(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_gov_api_key", "")
    with pytest.raises(agmarknet.AgmarknetError):
        agmarknet.run_daily(db, client=httpx.Client(), raw_dir=None)
    run = db.scalar(select(DataSourceRun).where(DataSourceRun.source == "agmarknet"))
    assert run.status == "failed" and "DATA_GOV_API_KEY" in run.error


def test_csv_backfill_header_mapping(db, tmp_path):
    csv = tmp_path / "hist.csv"
    csv.write_text(
        "Market Name,Commodity,Variety,Grade,Min Price (Rs./Quintal),Max Price (Rs./Quintal),"
        "Modal Price (Rs./Quintal),Price Date,Arrivals (Tonnes)\n"
        "Kolar APMC,Tomato,Local,FAQ,800,1600,1200,01 Aug 2025,410\n"
        "Kolar APMC,Onion,Local,FAQ,800,1600,1200,01 Aug 2025,50\n"
    )
    out = agmarknet.import_file(db, str(csv), default_state="Karnataka")
    assert out["stored"] == 1
    with pytest.raises(agmarknet.AgmarknetError):
        agmarknet.map_headers(["Mkt", "Price"])


def test_open_meteo_parse_and_store(db):
    payload = {  # shape per https://open-meteo.com/en/docs ; values made up
        "daily": {
            "time": ["2026-09-27", "2026-09-28", "2026-09-29"],
            "precipitation_sum": [4.2, 0.0, 12.5],
            "temperature_2m_max": [29.1, 30.4, 27.8],
            "temperature_2m_min": [20.0, 20.5, 19.9],
            "relative_humidity_2m_mean": [78, 70, 88],
        }
    }
    rows = weather.parse_open_meteo(payload, today=date(2026, 9, 28))
    assert [r["is_forecast"] for r in rows] == [False, False, True]

    with respx.mock:
        respx.get(get_settings().open_meteo_url).mock(return_value=httpx.Response(200, json=payload))
        out = weather.run_open_meteo(db, client=httpx.Client())
    n_mandis = len(db.scalars(select(Mandi).where(Mandi.lat.is_not(None))).all())
    assert out["rows"] == 3 * n_mandis


def test_nasa_power_fill_value_becomes_null():
    payload = {  # shape per NASA POWER daily point API docs; values made up
        "properties": {
            "parameter": {
                "T2M_MAX": {"20260901": 29.5, "20260902": -999.0},
                "T2M_MIN": {"20260901": 19.0, "20260902": 19.4},
                "PRECTOTCORR": {"20260901": 3.1, "20260902": 0.0},
                "RH2M": {"20260901": 80.0, "20260902": 76.0},
                "ALLSKY_SFC_SW_DWN": {"20260901": 4.9, "20260902": -999},
            }
        }
    }
    rows = weather.parse_nasa_power(payload)
    assert rows[1]["tmax_c"] is None and rows[1]["solar_kwh_m2"] is None and rows[0]["tmax_c"] == 29.5


def test_freshness_monitor(client, as_role, db):
    agmarknet.store_records(db, REAL["records"])
    db.add(DataSourceRun(source="agmarknet", status="success", rows=18,
                         finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
    db.commit()
    r = client.get("/admin/freshness", headers=as_role("admin")).json()
    by = {s["source"]: s for s in r["sources"]}
    assert by["agmarknet"]["status"] == "fresh" and by["open_meteo"]["status"] == "never"
    dq = client.get("/admin/data-quality", headers=as_role("admin")).json()
    assert any(row["mandi"] == "Belgaum APMC" and row["outliers"] == 1 for row in dq)


def test_latest_prices_near_a_farm(client, as_role, db):
    agmarknet.store_records(db, REAL["records"])
    db.commit()
    # a farm near Kolar
    r = client.get("/prices/latest", params={"near_lat": 13.1, "near_lon": 78.0, "radius_km": 80}, headers=as_role("farmer"))
    names = [x["mandi"]["name"] for x in r.json()]
    assert "Bangarpet APMC" in names and "Belgaum APMC" not in names
    assert r.json()[0]["distance_km"] <= r.json()[-1]["distance_km"]
    assert client.get("/prices/latest", headers=as_role("driver")).status_code == 403


def test_weekly_retrain_without_real_history_is_logged_not_crashing(db):
    """No real prices yet: the job must fail loudly in the run log (Admin → failed jobs), never train on synthetic."""
    from ingest.scheduler import job_retrain_weekly

    with pytest.raises(RuntimeError, match="No real price history"):
        job_retrain_weekly(db)
    run = db.scalar(select(DataSourceRun).where(DataSourceRun.source == "retrain"))
    assert run.status == "failed" and "No real price history" in run.error
