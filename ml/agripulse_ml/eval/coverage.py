"""Pre-V3 B-1: every evaluation run reports interval coverage against the target, automatically.

score() adds two metric rows per model x horizon (pooled and per mandi):
    coverage_gap_pct       coverage_p10_p90_pct - target (negative = ranges too narrow / overconfident)
    coverage_within_tol    1 if |gap| <= tolerance else 0
Target and tolerance come from config/calibration.toml (target_coverage_pct, tolerance_pct).
EvalRun.coverage_check() returns the pooled table; assert_coverage() raises CoverageDrift when a model drifts.
"""
import pandas as pd


class CoverageDrift(AssertionError):
    pass


def target_and_tolerance(target: float | None = None, tolerance: float | None = None) -> tuple[float, float]:
    if target is None or tolerance is None:
        from ..calibration import calibration_config

        c = calibration_config()["calibration"]
        target = float(c["target_coverage_pct"]) if target is None else target
        tolerance = float(c["tolerance_pct"]) if tolerance is None else tolerance
    return float(target), float(tolerance)


def coverage_rows(coverage_pct: float | None, target: float, tolerance: float) -> list[tuple[str, float | None]]:
    if coverage_pct is None or pd.isna(coverage_pct):
        return [("coverage_gap_pct", None), ("coverage_within_tol", None)]
    gap = float(coverage_pct) - target
    return [("coverage_gap_pct", gap), ("coverage_within_tol", 1.0 if abs(gap) <= tolerance else 0.0)]


def coverage_check(results: pd.DataFrame, target: float | None = None, tolerance: float | None = None) -> pd.DataFrame:
    """Pooled (mandi == ALL) coverage per model x horizon with gap and pass/fail."""
    target, tolerance = target_and_tolerance(target, tolerance)
    r = results[(results["mandi"] == "ALL") & (results["metric_name"] == "coverage_p10_p90_pct")]
    out = r[["model_name", "horizon", "data_provenance", "metric_value"]].rename(columns={"metric_value": "coverage_pct"})
    out["target_pct"], out["tolerance_pct"] = target, tolerance
    out["gap_pct"] = out["coverage_pct"] - target
    out["within_tolerance"] = out["gap_pct"].abs() <= tolerance
    return out.reset_index(drop=True)


def assert_coverage(results: pd.DataFrame, models=None, target: float | None = None, tolerance: float | None = None):
    chk = coverage_check(results, target, tolerance)
    if models is not None:
        chk = chk[chk["model_name"].isin(models)]
    bad = chk[~chk["within_tolerance"]]
    if len(bad):
        lines = [f"{r.model_name} h{r.horizon}: {r.coverage_pct:.1f}% (target {r.target_pct:.0f} +/- {r.tolerance_pct:.0f})"
                 for r in bad.itertuples()]
        raise CoverageDrift("interval coverage outside tolerance:\n" + "\n".join(lines))
    return chk
