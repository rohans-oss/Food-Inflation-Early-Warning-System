"""V3-0 study: the OLD V1 rule-based recommender vs the OR-Tools optimizer on IDENTICAL decision problems (rule 21).

    python -m agripulse_ml.decision_study          # ~20-30 min -> docs/results/optimizer-*.csv|json

SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT. There are no real lots or trips yet, so decisions cannot be scored
on real data (rule 23: docs/optimizer-results.md says so per module).

- Prices: the 8 synthetic datasets of V2-0 (seeds 1-8). Forecasts: V1 LightGBM (the display model) with the B-1
  track-record calibration, on V2-0's final 8 folds (the walk-forward is extended back 13 folds for the calibration
  track record, as in calibration_study.py). Decisions use the 1-week forecast, as /recommend/best-mandi does.
- Decision days: 10 per dataset, evenly spaced over the scored folds.
- Scenarios per day (random but seeded): lots around real Karnataka tomato belts, vehicles based in fleet towns.
    sparse       6 lots,  8 vehicles  (plenty of trucks)
    medium      20 lots, 18 vehicles
    dense_tight 60 lots, trucks for ~70% of the tonnage (capacity binds)
- Methods: rule (V1 mandi per lot + nearest-truck dispatcher), rule_mandis_opt_trucks (V1 mandis, CP-SAT trucks:
  isolates the truck-assignment gain), optimizer (CP-SAT, p50), optimizer_p10 (risk-averse).
- Every plan is scored ex post at the price REALISED one week later (not the forecast it planned on), with one cost
  model for all methods. Mandi overload is counted as a violation; its price impact is NOT modelled (that would
  favour the optimizer by construction, since the rule cannot see it).
- Distances: straight-line x road factor at the fallback speed (no OSRM here): "approximate distances".
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from agripulse_api.decisions import MandiOption, Problem, evaluate, optimize, rule_plan
from agripulse_api.decisions.model import approximate_distance
from agripulse_api.decisions.rule import v1_choices
from agripulse_api.decisions.sample import DENSITY, scenario
from agripulse_api.supply import cost_config

from .calibration import apply_to_predictions, calibration_config
from .eval import FoldSpec, run
from .eval.baseline import DATA_END, DATA_START, synthetic_features
from .eval.folds import make_folds
from .models import LightGBMQuantileForecaster
from .synthetic import SYNTH_MANDIS, generate

from agripulse_api.provenance import SYNTHETIC

ROOT = Path(__file__).resolve().parents[2]
SEEDS = list(range(1, 9))
DAYS_PER_SEED = 10
V2_0_FOLDS, WARMUP_FOLDS = 8, 13
METHODS = ("rule", "rule_mandis_opt_trucks", "optimizer", "optimizer_p10")
# Karnataka tomato belts (lot pickups) and fleet towns (vehicle bases): approximate centres, jittered per lot
BELTS = [(13.14, 78.13), (13.40, 78.06), (13.17, 78.39), (13.43, 77.73), (14.23, 76.40), (14.79, 75.40),
         (15.85, 74.50), (12.10, 76.70)]
FLEET_TOWNS = [(13.14, 78.13), (13.43, 77.73), (12.97, 77.59), (14.23, 76.40), (15.36, 75.12)]
CAPACITIES, CAP_P = (2.5, 5.0, 9.0, 10.0), (0.3, 0.4, 0.2, 0.1)


CACHE = ROOT / "data" / "cache"


def forecasts(seed: int, cache: bool = True) -> pd.DataFrame:
    """Calibrated V1 LightGBM predictions on V2-0's final 8 folds for one synthetic dataset.
    Cached in data/cache/ (gitignored): the same deterministic run, ~4-5 min per dataset otherwise."""
    f = CACHE / f"decision_forecasts_seed{seed}.parquet"
    if cache and f.exists():
        return pd.read_parquet(f)
    out = _forecasts(seed)
    if cache:
        CACHE.mkdir(parents=True, exist_ok=True)
        out.to_parquet(f)
    return out


def _forecasts(seed: int) -> pd.DataFrame:
    feat = synthetic_features(seed=seed)
    spec = FoldSpec(n_folds=V2_0_FOLDS + WARMUP_FOLDS)
    r = run(feat, {"lightgbm_quantile": LightGBMQuantileForecaster}, feature_set="prices+weather",
            data_provenance=SYNTHETIC, spec=spec, per_mandi=False)
    scored = r.folds[-V2_0_FOLDS:]
    v20 = make_folds(feat["date"], FoldSpec(n_folds=V2_0_FOLDS))
    assert [f["cutoff"] for f in scored] == [str(f.cutoff.date()) for f in v20], "final folds differ from V2-0's"
    cal, _ = apply_to_predictions(r.predictions, spec.label_lag_days, calibration_config(), method="track_record")
    return cal[cal["date"] >= pd.Timestamp(scored[0]["cutoff"])]


def decision_days(seed: int, cfg: dict, n_days: int = DAYS_PER_SEED):
    """(day, mandis with forecasts, realised prices) for evenly spaced days of one dataset."""
    preds = forecasts(seed)
    _, weather, arrivals = generate(DATA_START, DATA_END, seed, SYNTH_MANDIS)
    ids = {m[0]: i + 1 for i, m in enumerate(SYNTH_MANDIS)}
    coords = {i + 1: (m[2], m[3], m[0]) for i, m in enumerate(SYNTH_MANDIS)}
    weather["mandi_id"], arrivals["mandi_id"] = weather["mandi"].map(ids), arrivals["mandi"].map(ids)
    weather["date"], arrivals["date"] = pd.to_datetime(weather["date"]), pd.to_datetime(arrivals["date"])
    ok = preds.dropna(subset=["y_h1", "q50_h1"]).groupby("date")["mandi_id"].nunique()
    days = ok[ok == len(SYNTH_MANDIS)].index.sort_values()
    days = [days[int(i)] for i in np.linspace(0, len(days) - 1, n_days)]
    for di, day in enumerate(days):
        d = preds[preds["date"] == day].set_index("mandi_id")
        wx = weather[weather["date"] == day].set_index("mandi_id")["tmax_c"]
        past = arrivals[(arrivals["date"] < day) & (arrivals["date"] >= day - pd.Timedelta(days=28))]
        typical = past.groupby("mandi_id")["tonnes"].median()
        mandis, realised = [], {}
        for mid, (lat, lon, name) in coords.items():
            r = d.loc[mid]
            px = float(r["price"])
            mandis.append(MandiOption(mid, name, lat, lon, *(px * float(np.exp(r[f"q{q}_h1"])) for q in (10, 50, 90)),
                                      temp_c=float(wx.get(mid, cfg["spoilage"]["default_temp_c"])),
                                      typical_daily_tons=float(typical.get(mid)) if mid in typical else None))
            realised[mid] = px * float(np.exp(r["y_h1"]))
        yield di, day, mandis, realised


def run_seed(seed: int, cfg: dict) -> list[dict]:
    rows = []
    for di, day, mandis, realised in decision_days(seed, cfg):
        forecast_p50 = {m.id: m.p50 for m in mandis}
        for density in DENSITY:
            rng = np.random.default_rng([seed, di, list(DENSITY).index(density)])
            lots, vehicles = scenario(rng, density)
            prob = Problem(lots, vehicles, mandis, approximate_distance, cfg)
            plans = (rule_plan(prob), optimize(prob, "p50", fixed_mandis=v1_choices(prob)),
                     optimize(prob, "p50"), optimize(prob, "p10"))
            for plan in plans:
                real = evaluate(prob, plan, realised)
                planned = evaluate(prob, plan, forecast_p50)
                real.pop("assignments")
                rows.append({"seed": seed, "day": str(day.date()), "density": density, "n_lots": len(lots),
                             "n_vehicles": len(vehicles), "tons_offered": round(sum(x.tons for x in lots), 2),
                             "vehicle_capacity": round(sum(v.capacity_tons for v in vehicles), 1),
                             **real, "planned_net_value": planned["net_value"], "data_provenance": SYNTHETIC})
    return rows


def main():
    cfg = cost_config()
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    rows, t0 = [], time.time()
    for seed in SEEDS:
        rows += run_seed(seed, cfg)
        df = pd.DataFrame(rows)
        s = df[df.seed == seed].pivot_table(index="density", columns="method", values="net_value", aggfunc="mean")
        print(f"seed {seed} ({time.time() - t0:.0f}s): mean realised net value\n{s.round(0)}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "optimizer-synthetic.csv", index=False)
    (out / "optimizer-meta.json").write_text(json.dumps({
        "seeds": SEEDS, "days_per_seed": DAYS_PER_SEED, "densities": {k: list(v) for k, v in DENSITY.items()},
        "config": {k: v for k, v in cfg.items() if k != "_path"}, "distances": "approximate (straight-line x road factor)",
        "forecast": "V1 LightGBM, B-1 track-record calibration, 1-week horizon", "scored_at": "realised price (h=1)",
        "data_provenance": SYNTHETIC}, indent=2, default=str))
    print("SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT")


if __name__ == "__main__":
    main()


def switch_decision(df: pd.DataFrame) -> dict:
    """The PRE-REGISTERED switch rule (written before the study ran, docs/optimizer-results.md): the optimizer becomes
    the default only if its mean realised net value over all scenarios is >= the rule's AND it has fewer constraint
    violations in total. Returns the evidence and the verdict ("optimizer" or "rule")."""
    g = df[df["method"].isin(["rule", "optimizer"])].groupby("method")
    net, viol = g["net_value"].mean(), g["violations_total"].sum()
    ok_net = net["optimizer"] >= net["rule"]
    ok_viol = viol["optimizer"] < viol["rule"]
    return {"mean_net_rule": float(net["rule"]), "mean_net_optimizer": float(net["optimizer"]),
            "violations_rule": int(viol["rule"]), "violations_optimizer": int(viol["optimizer"]),
            "net_ok": bool(ok_net), "violations_ok": bool(ok_viol),
            "verdict": "optimizer" if ok_net and ok_viol else "rule"}
