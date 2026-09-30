"""V2-5 `transit` feature group: only what was recorded before 00:00 IST of the issue date is used."""
import dataclasses
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from agripulse_ml.features.config import features_config
from agripulse_ml.features.inputs import Inputs
from agripulse_ml.features.store import build_table
from agripulse_ml.transit.features import TRANSIT, snapshot, transit_features

IST = timezone(timedelta(hours=5, minutes=30))
CFG = features_config()["transit"]


def _mandis():
    return pd.DataFrame({"mandi_id": [1, 2], "district": ["Kolar", "Mysuru"], "state": "Karnataka",
                         "lat": [13.137, 12.310], "lon": [78.129, 76.652]})


def _utc(x):
    x = pd.Timestamp(x)
    return x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC")


def _trip(tid, mandi, tons, start, end, dur_min=600):
    return {"trip_id": tid, "mandi_id": mandi, "load_tons": tons, "started_at": _utc(start),
            "ended_at": None if end is None else _utc(end), "origin_lat": 13.5, "origin_lon": 77.5,
            "planned_duration_min": dur_min, "is_simulated": False}


def _feat(trips, gps=None, days=("2026-06-02",)):
    df = pd.DataFrame({"mandi_id": 1, "date": pd.to_datetime(list(days))})
    g = gps if gps is not None else pd.DataFrame(columns=["trip_id", "recorded_at", "lat", "lon"])
    t = pd.DataFrame(trips)
    t["ended_at"] = pd.to_datetime(t["ended_at"], utc=True)
    return transit_features(df, t, g, _mandis(), CFG).set_index(["mandi_id", "date"])


def test_snapshot_is_midnight_ist():
    assert snapshot(pd.Timestamp("2026-06-02")) == pd.Timestamp("2026-06-01 18:30", tz="UTC")


def test_eta_never_uses_the_actual_arrival_time():
    """Two identical trips still on the road at the snapshot, one of which will arrive much later: same features."""
    S = snapshot(pd.Timestamp("2026-06-02"))
    a = _feat([_trip(1, 1, 5, S - pd.Timedelta(hours=2), S + pd.Timedelta(hours=3))])
    b = _feat([_trip(1, 1, 5, S - pd.Timedelta(hours=2), S + pd.Timedelta(days=4))])
    pd.testing.assert_frame_equal(a, b)
    assert a.iloc[0]["tr_tons_now"] == 5 and a.iloc[0]["tr_tons_eta_24"] == 5  # planned 10 h trip, 2 h in


def test_gps_after_the_snapshot_is_ignored_and_before_it_moves_the_eta():
    S = snapshot(pd.Timestamp("2026-06-02"))
    trips = [_trip(1, 1, 5, S - pd.Timedelta(hours=40), None, dur_min=60 * 30)]  # planned: arrived 10 h ago
    far = pd.DataFrame({"trip_id": [1], "recorded_at": [S - pd.Timedelta(minutes=5)], "lat": [16.0], "lon": [74.5]})
    later = pd.concat([far, pd.DataFrame({"trip_id": [1], "recorded_at": [S + pd.Timedelta(hours=1)],
                                          "lat": [13.14], "lon": [78.13]})])
    f_far, f_later = _feat(trips, far), _feat(trips, later)
    pd.testing.assert_frame_equal(f_far, f_later)  # the fix after S (at the mandi) changes nothing
    # last known fix ~500 road km away: ETA ~14 h at 35 km/h -> within 24 h
    assert f_far.iloc[0]["tr_tons_eta_24"] == 5
    near = _feat(trips, pd.DataFrame({"trip_id": [1], "recorded_at": [S - pd.Timedelta(minutes=5)], "lat": [20.0],
                                      "lon": [73.0]}))
    assert near.iloc[0]["tr_tons_eta_24"] == 0 and near.iloc[0]["tr_tons_eta_72"] == 5  # ~1,200 km away


def test_trips_starting_or_ending_after_the_snapshot():
    S = snapshot(pd.Timestamp("2026-06-02"))
    f = _feat([_trip(1, 1, 3, S + pd.Timedelta(minutes=1), None),           # not started at S: invisible
               _trip(2, 1, 7, S - pd.Timedelta(days=2), S - pd.Timedelta(days=1)),  # delivered yesterday: recent
               _trip(3, 1, 11, S - pd.Timedelta(days=9), S - pd.Timedelta(days=8))])  # too old for recent_7
    r = f.iloc[0]
    assert r["tr_tons_now"] == 0 and r["tr_trips_now"] == 0 and r["tr_tons_recent_7"] == 7 and r["tr_tracked"] == 1


@pytest.fixture(scope="module")
def tinputs():
    return Inputs.from_synthetic(seed=11, start=date(2023, 1, 1), end=date(2025, 3, 31), n_mandis=2)


def test_transit_features_unchanged_when_the_future_is_deleted(tinputs):
    """Truncate to what the database held at C + 1 00:00 IST: later trips gone, later arrivals unknown.
    (Synthetic trips arrive within the issue day, so this test cannot see an ETA leak; the two tests above do.)"""
    full = build_table(tinputs, "prices+transit")
    C = pd.Timestamp("2024-06-15")
    S = snapshot(C + pd.Timedelta(days=1))
    t = tinputs.transit_trips
    cut_trips = t[t["started_at"] < S].copy()
    cut_trips.loc[cut_trips["ended_at"] >= S, "ended_at"] = pd.NaT  # not arrived yet, as seen at S
    cut = dataclasses.replace(tinputs, transit_trips=cut_trips,
                              prices=tinputs.prices[tinputs.prices["date"] <= C],
                              arrivals=tinputs.arrivals[tinputs.arrivals["date"] <= C])
    part = build_table(cut, "prices+transit", as_of=C + pd.Timedelta(days=1))
    key = ["mandi_id", "date"]
    win = full.df[(full.df["date"] > C - pd.Timedelta(days=40)) & (full.df["date"] <= C + pd.Timedelta(days=1))]
    j = win[key + TRANSIT].merge(part.df[key + TRANSIT], on=key, suffixes=("", "_cut"))
    assert len(j) > 50 and (j["tr_tons_now"] > 0).mean() > 0.5
    for c in TRANSIT:
        np.testing.assert_allclose(j[c].to_numpy(float), j[f"{c}_cut"].to_numpy(float), equal_nan=True, err_msg=c)


def test_synthetic_transit_is_labelled_and_untracked_is_not_zero(tinputs):
    tb = build_table(tinputs, "prices+transit")
    assert tb.group_provenance["transit"] == "synthetic" and set(TRANSIT) <= set(tb.feature_columns)
    none = dataclasses.replace(tinputs, transit_trips=tinputs.transit_trips.iloc[0:0])
    tb0 = build_table(none, "prices+transit")
    assert (tb0.df["tr_tracked"] == 0).all() and tb0.df["tr_tons_now"].isna().all()  # unknown, not "no supply"


def test_real_trips_from_the_database_are_real_partial(db):
    from agripulse_api.models import GpsPoint, Mandi, Trip, Vehicle

    m = db.query(Mandi).filter(Mandi.name == "Kolar APMC").one()
    v = db.query(Vehicle).first()
    now = datetime.now(timezone.utc)
    t = Trip(vehicle_id=v.id, mandi_id=m.id, origin_lat=13.5, origin_lon=77.8, load_tons=4.0, status="in_progress",
             started_at=now - timedelta(days=1), planned_duration_min=300, pickup_qr_token="p-v25", delivery_qr_token="d-v25",
             is_simulated=False)
    db.add(t)
    db.flush()
    db.add(GpsPoint(trip_id=t.id, recorded_at=now - timedelta(hours=20), lat=13.3, lon=78.0))
    db.commit()
    inp = Inputs.from_db(db, synthetic=False)
    assert len(inp.transit_trips) == 1 and len(inp.transit_gps) == 1 and inp.transit_provenance == "real_partial"
    sim = Inputs.from_db(db, synthetic=True)
    assert t.id not in set(sim.transit_trips["trip_id"])  # real and simulated trips never mix
