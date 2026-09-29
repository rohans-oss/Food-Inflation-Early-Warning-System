"""V2-1 feature store: naming, publication lags, and leakage tests per group (V2 rule 12)."""
import dataclasses
import json
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
from sqlalchemy import select

from agripulse_api.config import get_settings
from agripulse_api.models import Mandi, Price, WeatherForecast
from agripulse_ml.eval import run
from agripulse_ml.features.build import main as build_cli
from agripulse_ml.features.config import FeatureGroupNotBuilt, feature_set_name, parse_feature_set
from agripulse_ml.features.inputs import Inputs
from agripulse_ml.features.store import CALENDAR, PRICES, WEATHER_FUTURE, WEATHER_PAST, build_table
from agripulse_ml.models import NaiveForecaster

START, END = date(2023, 1, 1), date(2025, 3, 31)


@pytest.fixture(scope="module")
def inputs():
    return Inputs.from_synthetic(seed=11, start=START, end=END, n_mandis=2)


@pytest.fixture(scope="module")
def table(inputs):
    return build_table(inputs, "prices+weather")


def _at(tb, mandi, day):
    r = tb.df[(tb.df["mandi_id"] == mandi) & (tb.df["date"] == pd.Timestamp(day))]
    assert len(r) == 1, f"no row for mandi {mandi} on {day}"
    return r.iloc[0]


def _same(a: pd.Series, b: pd.Series, cols) -> list[str]:
    bad = []
    for c in cols:
        x, y = a[c], b[c]
        if not ((pd.isna(x) and pd.isna(y)) or np.isclose(float(x), float(y), equal_nan=True)):
            bad.append(f"{c}: {x} != {y}")
    return bad


def _replace(inp: Inputs, **frames) -> Inputs:
    return dataclasses.replace(inp, **frames)


# ---------------------------------------------------------------- naming


def test_feature_set_naming():
    assert feature_set_name("weather+prices") == "prices+weather"
    assert feature_set_name(["prices", "calendar"]) == "prices"  # calendar is always on, not in the name
    assert parse_feature_set("prices+weather+transit") == ["prices", "weather", "transit"]
    with pytest.raises(ValueError):
        parse_feature_set("prices+vibes")


def test_pending_groups_refuse_and_prices_required(inputs):
    for fs in ("prices+satellite", "prices+graph", "prices+transit"):
        with pytest.raises(FeatureGroupNotBuilt):
            build_table(inputs, fs)
    with pytest.raises(ValueError):
        build_table(inputs, "weather")


def test_column_roles(table):
    assert set(table.columns["known_future"]) == set(CALENDAR + WEATHER_FUTURE)
    assert set(table.columns["past_only"]) == set(PRICES + WEATHER_PAST)
    assert "target_h1" not in table.feature_columns and "spike" not in table.feature_columns
    only_prices = build_table(Inputs.from_synthetic(seed=11, start=START, end=END, n_mandis=1), "prices")
    assert not set(WEATHER_PAST + WEATHER_FUTURE) & set(only_prices.feature_columns)


# ---------------------------------------------------------------- leakage: truncation, per group


@pytest.mark.parametrize("group_cols", [("prices", PRICES), ("weather-past", WEATHER_PAST),
                                        ("weather-forecast", WEATHER_FUTURE), ("calendar", CALENDAR)],
                         ids=lambda g: g[0])
def test_features_unchanged_when_the_future_is_deleted(inputs, table, group_cols):
    """Build "as of" C + 1: delete every observation dated after C (1-day publication lag) and every forecast
    issued after C + 1. Features at issue dates up to C + 1 must not change."""
    name, cols = group_cols
    C = pd.Timestamp("2024-06-15")
    cut = _replace(inputs,
                   prices=inputs.prices[inputs.prices["date"] <= C],
                   arrivals=inputs.arrivals[inputs.arrivals["date"] <= C],
                   weather=inputs.weather[inputs.weather["date"] <= C],
                   # a forecast issued ON the issue date is known that day (0-day lag), so keep up to C + 1
                   forecasts=inputs.forecasts[inputs.forecasts["issued_on"] <= C + pd.Timedelta(days=1)])
    tb2 = build_table(cut, "prices+weather", as_of=C + pd.Timedelta(days=1))  # "today" is C + 1
    window = (table.df["date"] >= C - pd.Timedelta(days=40)) & (table.df["date"] <= C + pd.Timedelta(days=1))
    rows = table.df.loc[window, ["mandi_id", "date"]]
    assert len(rows) > 50 and (rows["date"] == C + pd.Timedelta(days=1)).any()
    bad = []
    for m, t in rows.itertuples(index=False):
        bad += [f"{t.date()} m{m} {x}" for x in _same(_at(table, m, t), _at(tb2, m, t), cols)]
    assert not bad, f"{name} features use data published after t:\n" + "\n".join(bad[:10])


# ---------------------------------------------------------------- leakage: publication lags


def test_same_day_price_is_not_used(inputs, table):
    """Prices publish with a 1-day lag: changing the price ON day t must not change anything at t."""
    p = inputs.prices.copy()
    obs = set(p.loc[p["mandi_id"] == 1, "date"])
    # a day with an observed price, and prices on the day after too (so t+1 has a row that sees it)
    t = next(d for d in sorted(obs) if d >= pd.Timestamp("2024-03-01") and d + pd.Timedelta(days=1) in obs)
    p.loc[(p["mandi_id"] == 1) & (p["date"] == t), "price"] *= 10
    assert ((p["mandi_id"] == 1) & (p["date"] == t)).any(), "fixture needs a price on t"
    tb2 = build_table(_replace(inputs, prices=p), "prices+weather")
    assert not _same(_at(table, 1, t), _at(tb2, 1, t), table.feature_columns + ["price"])
    # ...but it is visible one day later
    assert _same(_at(table, 1, t + pd.Timedelta(days=1)), _at(tb2, 1, t + pd.Timedelta(days=1)), ["px_chg_1"])


def test_nasa_power_three_day_lag(inputs):
    """NASA POWER publishes ~3 days late: its values for t-1 and t-2 must not reach features at t;
    t-3 must. An Open-Meteo value for t-1 (1-day lag) is usable at t."""
    w = inputs.weather[inputs.weather["mandi_id"] == 1].assign(source="nasa_power")
    base = _replace(inputs, weather=w, forecasts=inputs.forecasts.iloc[0:0])
    t = pd.Timestamp("2024-08-20")
    tb = build_table(base, "prices+weather")

    def bumped(days_back, source="nasa_power"):
        ww = w.copy()
        d = t - pd.Timedelta(days=days_back)
        if source == "nasa_power":
            ww.loc[ww["date"] == d, "precip_mm"] += 500
        else:
            ww = pd.concat([ww, pd.DataFrame([{"mandi_id": 1, "date": d, "source": "open_meteo", "precip_mm": 999.0, "tmax_c": 30.0}])])
        return _at(build_table(_replace(base, weather=ww), "prices+weather"), 1, t)

    ref = _at(tb, 1, t)
    assert not _same(ref, bumped(1), WEATHER_PAST), "NASA value from t-1 leaked into t"
    assert not _same(ref, bumped(2), WEATHER_PAST), "NASA value from t-2 leaked into t"
    assert _same(ref, bumped(3), ["wx_rain_7"]), "NASA value from t-3 should be usable at t"
    assert _same(ref, bumped(1, "open_meteo"), ["wx_rain_7"]), "Open-Meteo value from t-1 should be usable at t"


def test_forecasts_issued_after_t_are_ignored(inputs, table):
    """No forecast issued on t itself; an extreme one issued on t+1. Features at t must come from the
    t-1 forecast (age 1), never from t+1."""
    t = pd.Timestamp("2024-05-05")
    fc = inputs.forecasts
    m1 = fc["mandi_id"] == 1
    extra = pd.DataFrame([{"mandi_id": 1, "issued_on": t + pd.Timedelta(days=1), "target_date": t + pd.Timedelta(days=k),
                           "precip_mm": 999.0, "tmax_c": 60.0} for k in range(2, 18)])
    fc = pd.concat([fc[~(m1 & fc["issued_on"].isin([t, t + pd.Timedelta(days=1)]))], extra])
    r = _at(build_table(_replace(inputs, forecasts=fc), "prices+weather"), 1, t)
    assert r["wf_age_days"] == 1, "features at t must use the forecast issued on t-1"
    assert r["wf_tmax_next7"] < 50 and r["wf_rain_next7"] < 900, "a forecast issued after t leaked into t"


def test_forecast_archive_gaps(inputs):
    """Real archives have gaps: a forecast up to 3 days old is used (with its age); older -> missing."""
    t = pd.Timestamp("2024-05-20")
    fc = inputs.forecasts
    fc = fc[~((fc["issued_on"] > t - pd.Timedelta(days=2)) & (fc["issued_on"] <= t + pd.Timedelta(days=5)))]
    tb = build_table(_replace(inputs, forecasts=fc), "prices+weather")
    r = _at(tb, 1, t)
    assert r["wf_available"] == 1 and r["wf_age_days"] == 2
    r = _at(tb, 1, t + pd.Timedelta(days=5))  # latest issue is 7 days old
    assert r["wf_available"] == 0 and pd.isna(r["wf_rain_next7"])


# ---------------------------------------------------------------- targets, labels, folds


def test_targets_are_relative_to_the_published_price(inputs, table):
    t = pd.Timestamp("2024-02-14")
    grid = (inputs.prices[inputs.prices["mandi_id"] == 1].set_index("date")["price"]
            .reindex(pd.date_range(START, END)).ffill(limit=3))
    r = _at(table, 1, t)
    assert r["price"] == grid[t - pd.Timedelta(days=1)]
    assert np.isclose(r["target_h1"], np.log(grid[t + pd.Timedelta(days=7)] / grid[t - pd.Timedelta(days=1)]))


def test_calendar_is_known_future(table):
    from agripulse_ml.features.legacy import festival_flag

    for t in pd.date_range("2024-09-01", "2024-10-10", freq="3D"):
        nxt = festival_flag(pd.Series(pd.date_range(t + pd.Timedelta(days=1), t + pd.Timedelta(days=14)))).max()
        assert _at(table, 1, t)["cal_festival_next14"] == nxt


def test_folds_train_only_on_published_labels(table):
    spec = table.fold_spec(min_train_days=300, n_folds=2)
    assert spec.label_lag_days == 1
    r = run(table.df, {"naive": NaiveForecaster}, table.feature_set, table.mandi_provenance, spec=spec)
    assert r.data_provenance == "synthetic" and len(r.folds) >= 1
    for f in r.folds:
        assert pd.Timestamp(f["train_max_target_date"]) + pd.Timedelta(days=1) < pd.Timestamp(f["cutoff"])


# ---------------------------------------------------------------- provenance, DB path, CLI, archive ingest


def test_real_db_inputs_are_real_partial_and_exclude_synthetic(db):
    from agripulse_ml.synthetic import load_into_db

    load_into_db(db, start=date(2025, 1, 1), end=date(2025, 12, 31))
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    for i in range(90):  # 90 real days: far below the 365-day readiness threshold
        db.add(Price(mandi_id=kolar.id, commodity="Tomato", variety="Local", grade="FAQ",
                     date=date(2026, 6, 1) + timedelta(days=i), modal_price=1500 + i, source="agmarknet"))
    db.commit()
    inp = Inputs.from_db(db, synthetic=False)
    assert set(inp.prices["mandi_id"]) == {kolar.id} and len(inp.prices) == 90
    assert any("no archived real weather forecasts" in n for n in inp.notes)
    tb = build_table(inp, "prices+weather")
    assert tb.data_provenance == "real_partial" and tb.group_provenance["prices"] == "real_partial"
    assert tb.df["wf_available"].eq(0).all()
    syn = build_table(Inputs.from_db(db, synthetic=True), "prices")
    assert syn.data_provenance == "synthetic" and kolar.id in syn.mandi_provenance


def test_build_cli(tmp_path, capsys):
    rc = build_cli(["--feature-set", "weather+prices", "--provenance", "synthetic", "--seed", "3", "--out", str(tmp_path)])
    assert rc == 0
    pq = tmp_path / "prices+weather__synthetic__seed3.parquet"
    card = json.loads(pq.with_suffix(".card.json").read_text())
    assert card["data_provenance"] == "synthetic" and card["provenance_label"].startswith("SYNTHETIC")
    assert card["label_lag_days"] == 1 and card["rows"] == len(pd.read_parquet(pq))
    out = capsys.readouterr().out
    assert "SYNTHETIC — METHODOLOGY DEMO" in out and "missing" in out
    assert build_cli(["--feature-set", "prices+graph", "--provenance", "synthetic", "--out", str(tmp_path)]) == 2


def test_open_meteo_forecasts_are_archived_as_issued(db):
    from ingest import weather

    today = weather.datetime.now(weather.IST).date()
    days = [today - timedelta(days=1)] + [today + timedelta(days=k) for k in range(0, 4)]
    payload = {"daily": {"time": [d.isoformat() for d in days], "precipitation_sum": [1, 2, 3, 4, 5],
                         "temperature_2m_max": [30] * 5, "temperature_2m_min": [20] * 5,
                         "relative_humidity_2m_mean": [70] * 5}}
    with respx.mock:
        respx.get(get_settings().open_meteo_url).mock(return_value=httpx.Response(200, json=payload))
        weather.run_open_meteo(db, client=httpx.Client())
        payload["daily"]["precipitation_sum"] = [9, 9, 9, 9, 9]  # a later fetch the same day replaces, not duplicates
        respx.get(get_settings().open_meteo_url).mock(return_value=httpx.Response(200, json=payload))
        weather.run_open_meteo(db, client=httpx.Client())
    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    rows = db.scalars(select(WeatherForecast).where(WeatherForecast.mandi_id == kolar.id)).all()
    assert {r.issued_on for r in rows} == {today}
    assert sorted(r.lead_days for r in rows) == [1, 2, 3]  # only future days (today is observed/partial)
    assert all(r.precip_mm == 9 for r in rows)


def test_forecast_archive_readiness(db):
    from agripulse_api.readiness import compute

    kolar = db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))
    d0 = date(2026, 9, 20)
    for i in range(5):
        db.add(WeatherForecast(mandi_id=kolar.id, issued_on=d0 + timedelta(days=i), target_date=d0 + timedelta(days=i + 1),
                               lead_days=1, source="open_meteo", precip_mm=1.0, tmax_c=30.0))
    db.commit()
    r = next(m for m in compute(db, date(2026, 9, 25))["mandis"] if m["mandi_id"] == kolar.id)["weather_forecasts"]
    assert r["history_days"] == 5 and r["status"] == "collecting" and r["projected_ready_date"] == d0 + timedelta(days=364)
