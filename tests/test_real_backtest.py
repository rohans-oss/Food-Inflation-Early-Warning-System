"""V3-4 real backtest: pre-registered events, lead time, false alarms, the minimum-evidence gate, no look-ahead in the
threshold rule, and the real path never reading synthetic rows."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from agripulse_api.models import Mandi, Price
from agripulse_ml.features import build_features
from agripulse_ml.real_backtest import PREREG, earliest_first_fold, find_events, fold_thresholds, run_real, score_events

D0 = pd.Timestamp("2025-01-01")


def _series(jumps: list[int], n: int = 200, mid: int = 1) -> pd.DataFrame:
    """Flat 1000 Rs/q with a +50% jump on each day index in `jumps` lasting 10 days."""
    p = np.full(n, 1000.0)
    for j in jumps:
        p[j:j + 10] = 1500.0
    return pd.DataFrame({"mandi_id": mid, "date": D0 + pd.to_timedelta(np.arange(n), "D"), "price": p})


def test_one_jump_is_one_event_with_the_right_rise_date():
    feat = build_features(_series([60]))
    ev = find_events(feat)
    assert len(ev) == 1
    e = ev.iloc[0]
    assert e.rise_date == D0 + pd.Timedelta(days=60)
    assert e.start == D0 + pd.Timedelta(days=60 - PREREG["spike_window_days"])  # first day that sees it 14 days ahead
    assert e.rise_pct == 50.0
    assert len(find_events(build_features(_series([60, 64])))) == 1  # runs < 7 days apart merge


def _preds(n=200, mid=1, prob=0.0):
    return pd.DataFrame({"mandi_id": mid, "date": D0 + pd.to_timedelta(np.arange(n), "D"), "spike_prob": prob})


def test_lead_time_false_alarms_and_the_minimum_evidence_gate():
    feat = build_features(_series([60]))
    ev = find_events(feat)
    p = _preds()
    p.loc[p["date"] == D0 + pd.Timedelta(days=55), "spike_prob"] = 0.9  # 5 days ahead of the rise
    p.loc[p["date"] == D0 + pd.Timedelta(days=150), "spike_prob"] = 0.9  # nothing follows: a false alarm
    t, s = score_events(p, ev, np.full(len(p), 0.5))
    assert bool(t.iloc[0].detected) and t.iloc[0].lead_days == 5
    assert s["false_alarm_days"] == 1 and s["alert_days"] == 2
    assert s["withheld"] and s["recall"] is None  # 1 event < 5: numbers withheld, counts kept


def test_five_events_pass_the_gate_and_an_always_on_alarm_is_exposed():
    jumps = [40, 90, 140, 190, 240]
    feat = build_features(_series(jumps, n=300))
    ev = find_events(feat)
    assert len(ev) == 5
    t, s = score_events(_preds(300, prob=1.0), ev, np.full(300, 0.5))
    assert not s["withheld"] and s["recall"] == 1.0
    assert s["alert_day_share_pct"] == 100.0 and s["alert_precision"] < 0.5 and s["false_alarm_days_per_mandi_year"] > 100
    t, s = score_events(_preds(300, prob=0.0), ev, np.full(300, 0.5))
    assert s["recall"] == 0.0 and s["false_alarm_days"] == 0


def test_an_event_without_forecasts_over_its_whole_window_is_not_scored():
    feat = build_features(_series([60]))
    p = _preds()
    p = p[p["date"] >= D0 + pd.Timedelta(days=50)]  # forecasts start inside the window
    t, s = score_events(p, find_events(feat), np.full(len(p), 0.5))
    assert s["events_scoreable"] == 0


def test_fold_threshold_uses_only_labels_known_before_its_cutoff():
    rng = np.random.default_rng(0)
    n = 400
    p = pd.DataFrame({"date": D0 + pd.to_timedelta(np.arange(n), "D"), "spike": (rng.random(n) < 0.2).astype(float)})
    p["spike_prob"] = np.clip(p["spike"] * 0.6 + rng.random(n) * 0.4, 0, 1)
    folds = [{"fold": 0, "cutoff": str((D0 + pd.Timedelta(days=100)).date())},
             {"fold": 1, "cutoff": str((D0 + pd.Timedelta(days=300)).date())}]
    before = fold_thresholds(p, folds)
    cutoff = pd.Timestamp(folds[1]["cutoff"])
    unknown = p["date"] + pd.Timedelta(days=PREREG["spike_window_days"] + PREREG["label_lag_days"]) >= cutoff
    tampered = p.copy()
    tampered.loc[unknown, "spike"] = 1 - tampered.loc[unknown, "spike"]  # rewrite the future
    tampered.loc[unknown, "spike_prob"] = 1 - tampered.loc[unknown, "spike_prob"]
    assert fold_thresholds(tampered, folds) == before
    assert fold_thresholds(p.iloc[:30], folds)[0] == PREREG["fixed_threshold"]  # no track record -> fixed 0.5


def test_real_path_ignores_synthetic_rows_and_says_when_it_can_run(db):
    m = db.query(Mandi).first()
    for k in range(400):
        db.add(Price(mandi_id=m.id, commodity="Tomato", date=date(2024, 1, 1) + timedelta(days=k), modal_price=1500,
                     source="synthetic"))
    db.commit()
    out = run_real(db)
    assert out["status"] == "not_enough_real_data" and out["inventory"]["real_price_rows"] == 0
    db.add(Price(mandi_id=m.id, commodity="Tomato", date=date(2026, 9, 25), modal_price=1800, source="agmarknet"))
    db.commit()
    out = run_real(db)
    assert out["status"] == "not_enough_real_data" and out["inventory"]["real_price_rows"] == 1
    assert out["earliest_first_fold"] == str(earliest_first_fold(date(2026, 9, 25))) == "2027-10-24"


def test_real_path_runs_end_to_end_once_history_exists(db):
    """Planted real-source history (as a backfill would give): the backtest runs and labels itself real/real_partial."""
    rng = np.random.default_rng(1)
    mandis = db.query(Mandi).limit(2).all()
    n = 480
    for m in mandis:
        p = 1500 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        for j in range(30, n, 45):
            p[j:j + 8] *= 1.6
        for k in range(n):
            db.add(Price(mandi_id=m.id, commodity="Tomato", date=date(2024, 6, 1) + timedelta(days=k),
                         modal_price=float(p[k]), source="agmarknet"))
    db.commit()
    out = run_real(db)
    assert out["status"] == "ran", out.get("reason")
    assert out["data_provenance"] in ("real", "real_partial") and out["events_found"] > 0
    tot = out["totals"]
    assert set(tot["model_name"]) == {"naive", "seasonal_naive", "lightgbm_quantile"}
    assert set(tot["threshold_rule"]) == {"fixed_0.5", "earlier_folds_f1"}
    assert "lightgbm_quantile_calibrated|1|coverage_p10_p90_pct" in out["intervals"]
