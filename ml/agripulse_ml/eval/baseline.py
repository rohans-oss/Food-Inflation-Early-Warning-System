"""V2 synthetic baseline: seasonal naive, naive and V1 LightGBM through the shared harness.

    python -m agripulse_ml.eval.baseline                # writes docs/backtest-synthetic.md + docs/results/*.csv
    python -m agripulse_ml.eval.baseline --record       # also records to the DB (DATABASE_URL) and MLflow
    python -m agripulse_ml.eval.baseline --seeds 1-8    # + robustness across synthetic draws (slow: ~40 s/seed)

Why seeds: one synthetic draw is one random history. The V1 claim "LightGBM beats naive by 1-2% /
8-12%" reproduced exactly on its own draw but not on others, so any synthetic model comparison in V2
must be reported across several draws, not one.

The dataset is PINNED (generator seed 7, 2022-01-01 .. 2026-09-25, 18 seeded mandis), built in
memory, so every V2 model is compared on exactly these rows and folds. Everything here is
data_provenance = "synthetic".
"""
import argparse
import os
from datetime import date
from pathlib import Path

import pandas as pd

from agripulse_api.provenance import LABEL, SYNTHETIC

from ..evaluate import V1_FEATURE_SET, V1_MODELS
from ..features import build_features
from ..models import add_seasonal_naive
from ..synthetic import SYNTH_MANDIS, generate
from . import FoldSpec, run

DATA_START, DATA_END, SEED = date(2022, 1, 1), date(2026, 9, 25), 7
ROOT = Path(__file__).resolve().parents[3]


def synthetic_features(seed: int | None = None, end: date | None = None) -> pd.DataFrame:
    """The pinned synthetic training table. mandi_id = index in SYNTH_MANDIS + 1 (stable across runs)."""
    p, w, a = generate(DATA_START, end or DATA_END, SEED if seed is None else seed, SYNTH_MANDIS)
    ids = {m[0]: i + 1 for i, m in enumerate(SYNTH_MANDIS)}
    for d in (p, w, a):
        d["mandi_id"] = d["mandi"].map(ids)
    return build_features(p[["mandi_id", "date", "price"]], w[["mandi_id", "date", "precip_mm", "tmax_c"]],
                          a[["mandi_id", "date", "tonnes"]])


def evaluate(n_folds: int):
    feat = synthetic_features()
    return run(feat, V1_MODELS, feature_set=V1_FEATURE_SET, data_provenance=SYNTHETIC, spec=FoldSpec(n_folds=n_folds),
               prepare=add_seasonal_naive)


def _table(r) -> str:
    pooled = r.results[r.results["mandi"] == "ALL"]
    v = {(m, h, k): x for m, h, k, x in pooled[["model_name", "horizon", "metric_name", "metric_value"]].itertuples(index=False)}
    lines = ["| Horizon | Seasonal naive | Naive | LightGBM (V1) | LightGBM vs naive | LightGBM vs seasonal naive | LightGBM p10–p90 coverage | LightGBM MAPE |",
             "|---|---|---|---|---|---|---|---|"]
    for h in "1234":
        sn, nv, lg = (v[(m, h, "pinball_mean")] for m in ("seasonal_naive", "naive", "lightgbm_quantile"))
        lines.append(f"| {h} wk | {sn:.1f} | {nv:.1f} | {lg:.1f} | {100 * (1 - lg / nv):+.1f}% | {100 * (1 - lg / sn):+.1f}% "
                     f"| {v[('lightgbm_quantile', h, 'coverage_p10_p90_pct')]:.1f}% | {v[('lightgbm_quantile', h, 'mape_p50_pct')]:.1f}% |")
    lines += ["", "| Model | Spike events | Recall | Precision | False-alarm rate | Brier |", "|---|---|---|---|---|---|"]
    fmt = lambda x: "–" if pd.isna(x) else f"{x:.3f}"  # noqa: E731
    for m in ("seasonal_naive", "naive", "lightgbm_quantile"):
        g = lambda k: v.get((m, "spike_14d", k))  # noqa: E731
        lines.append(f"| {m} | {int(g('spike_events'))} | {fmt(g('spike_recall'))} | {fmt(g('spike_precision'))} "
                     f"| {fmt(g('spike_false_alarm_rate'))} | {fmt(g('spike_brier'))} |")
    return "\n".join(lines)


def vs_naive(r) -> dict[str, float]:
    p = r.results[(r.results["mandi"] == "ALL") & (r.results["metric_name"] == "pinball_mean")]
    x = p.pivot_table(index="horizon", columns="model_name", values="metric_value")
    return {h: round(100 * (1 - x.loc[h, "lightgbm_quantile"] / x.loc[h, "naive"]), 1) for h in "1234"}


def seed_sweep(seeds: list[int], models: dict, feature_set: str, n_folds: int = 8, prepare=None) -> pd.DataFrame:
    """Pooled metrics per synthetic draw: columns seed, model_name, horizon, metric_name, metric_value."""
    frames = []
    for sd in seeds:
        r = run(synthetic_features(seed=sd), models, feature_set=feature_set, data_provenance=SYNTHETIC,
                spec=FoldSpec(n_folds=n_folds), per_mandi=False, prepare=prepare)
        frames.append(r.results.assign(seed=sd))
    return pd.concat(frames, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="also record to DB + MLflow")
    ap.add_argument("--seeds", default="", help="e.g. 1-8: also run naive vs LightGBM across synthetic draws")
    args = ap.parse_args()
    main_run = evaluate(8)
    check_run = evaluate(3)  # the fold count behind the V1 README's "8-12% at 3-4 weeks"
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    csv = out / "backtest-synthetic.csv"
    main_run.results.to_csv(csv, index=False)
    if args.record:
        from agripulse_api.db import SessionLocal

        from .tracking import log_mlflow, record

        mid = log_mlflow(main_run, purpose="v2_baseline", artifacts={"results": str(csv)})
        with SessionLocal() as db:
            record(db, main_run, purpose="v2_baseline", mlflow_run_id=mid)
    print(LABEL[SYNTHETIC])
    print("8 folds, LightGBM vs naive pinball:", vs_naive(main_run))
    print("3 folds, LightGBM vs naive pinball:", vs_naive(check_run))
    print(_table(main_run))
    # the markdown report is written by the doc template, filled from these runs
    (out / "backtest-synthetic-tables.md").write_text(
        f"<!-- generated by python -m agripulse_ml.eval.baseline; {LABEL[SYNTHETIC]} -->\n\n"
        f"## 8 folds (standard)\n\n{_table(main_run)}\n\n## 3 folds (V1 README run)\n\n{_table(check_run)}\n\n"
        f"Folds (8): {', '.join(f['cutoff'] for f in main_run.folds)} (each 28 days)\n")
    if args.seeds:
        a, _, z = args.seeds.partition("-")
        seeds = list(range(int(a), int(z or a) + 1))
        from ..models import LightGBMQuantileForecaster, NaiveForecaster

        sweep = seed_sweep(seeds, {"naive": NaiveForecaster, "lightgbm_quantile": LightGBMQuantileForecaster}, V1_FEATURE_SET)
        sweep.to_csv(out / "backtest-synthetic-seeds.csv", index=False)
        pin = sweep[sweep["metric_name"] == "pinball_mean"].pivot_table(index=["seed", "horizon"], columns="model_name",
                                                                         values="metric_value")
        gain = (100 * (1 - pin["lightgbm_quantile"] / pin["naive"])).unstack("horizon")
        print("LightGBM vs naive pinball, % better, per seed:\n", gain.round(1))
        print("mean:", gain.mean().round(1).to_dict(), "wins:", (gain > 0).sum().to_dict())


if __name__ == "__main__":
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    main()
