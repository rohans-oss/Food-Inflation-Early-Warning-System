"""V3-1 study: shared truckloads and return loads, vs V3-0's optimizer and the V1 rule, on identical batches.

    python -m agripulse_ml.consolidation_study     # forecasts cached by decision_study -> docs/results/consolidation-*

SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT. Same datasets, calibrated forecasts, realised-price scoring and cost
model as V3-0 (decision_study.py). Decision days: every second V3-0 day (5 per dataset, 40 in all) to fit the
compute budget. Batches: V3-0's three sizes, each with lots SPREAD over 8 belts or CLUSTERED in 2.

Methods: rule (V1) | optimizer (V3-0, one lot per truck) | optimizer+returns | optimizer_shared |
optimizer_shared+returns. Pre-registered switch (written before the run): [consolidation] and [return_loads] are
turned on for users only if optimizer_shared+returns earns >= the V3-0 optimizer on mean realised net value AND has
no more constraint violations.
"""
import json
import time

import numpy as np
import pandas as pd

from agripulse_api.decisions import Problem, evaluate, optimize, rule_plan
from agripulse_api.decisions.loads import optimize_loads
from agripulse_api.decisions.model import approximate_distance
from agripulse_api.decisions.returns import add_return_loads
from agripulse_api.decisions.sample import DENSITY, scenario
from agripulse_api.provenance import SYNTHETIC
from agripulse_api.supply import cost_config

from .decision_study import DAYS_PER_SEED, ROOT, SEEDS, decision_days

LAYOUTS = ("spread", "clustered")
METHODS = ("rule", "optimizer", "optimizer+returns", "optimizer_shared", "optimizer_shared+returns")
DAY_STEP = 2  # every second V3-0 decision day


def run_seed(seed: int, cfg: dict) -> list[dict]:
    rows = []
    for di, day, mandis, realised in decision_days(seed, cfg, DAYS_PER_SEED):
        if di % DAY_STEP:
            continue
        for density in DENSITY:
            for layout in LAYOUTS:
                rng = np.random.default_rng([seed, di, list(DENSITY).index(density), 100 + LAYOUTS.index(layout)])
                lots, vehicles = scenario(rng, density, clustered=layout == "clustered")
                prob = Problem(lots, vehicles, mandis, approximate_distance, cfg)
                v30 = optimize(prob, "p50")
                shared, _ = optimize_loads(prob, "p50")
                for plan in (rule_plan(prob), v30, add_return_loads(prob, v30), shared, add_return_loads(prob, shared)):
                    real = evaluate(prob, plan, realised)
                    real.pop("assignments")
                    rows.append({"seed": seed, "day": str(day.date()), "density": density, "layout": layout,
                                 "n_lots": len(lots), "n_vehicles": len(vehicles),
                                 "tons_offered": round(sum(x.tons for x in lots), 2),
                                 "vehicle_capacity": round(sum(v.capacity_tons for v in vehicles), 1),
                                 **real, "data_provenance": SYNTHETIC})
    return rows


def switch_decision(df: pd.DataFrame) -> dict:
    """The PRE-REGISTERED V3-1 switch: shared + return loads on only if mean realised net >= V3-0 optimizer's and no
    more violations."""
    g = df[df["method"].isin(["optimizer", "optimizer_shared+returns"])].groupby("method")
    net, viol = g["net_value"].mean(), g["violations_total"].sum()
    ok_net = net["optimizer_shared+returns"] >= net["optimizer"]
    ok_viol = viol["optimizer_shared+returns"] <= viol["optimizer"]
    return {"mean_net_v30": float(net["optimizer"]), "mean_net_v31": float(net["optimizer_shared+returns"]),
            "violations_v30": int(viol["optimizer"]), "violations_v31": int(viol["optimizer_shared+returns"]),
            "net_ok": bool(ok_net), "violations_ok": bool(ok_viol), "enable": bool(ok_net and ok_viol)}


def main():
    cfg = cost_config()
    out = ROOT / "docs" / "results"
    rows, t0 = [], time.time()
    for seed in SEEDS:
        rows += run_seed(seed, cfg)
        df = pd.DataFrame(rows)
        s = df[df.seed == seed].pivot_table(index=["density", "layout"], columns="method", values="net_value")
        print(f"seed {seed} ({time.time() - t0:.0f}s)\n{s.round(0)}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "consolidation-synthetic.csv", index=False)
    (out / "consolidation-meta.json").write_text(json.dumps({
        "seeds": SEEDS, "days": f"every {DAY_STEP}nd of V3-0's {DAYS_PER_SEED} per dataset", "layouts": LAYOUTS,
        "densities": {k: list(v) for k, v in DENSITY.items()}, "methods": METHODS,
        "config": {k: v for k, v in cfg.items() if k != "_path"}, "switch": switch_decision(df),
        "distances": "approximate (straight-line x road factor)", "scored_at": "realised price (h=1)",
        "data_provenance": SYNTHETIC}, indent=2, default=str))
    print(json.dumps(switch_decision(df), indent=1))
    print("SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT")


if __name__ == "__main__":
    main()
