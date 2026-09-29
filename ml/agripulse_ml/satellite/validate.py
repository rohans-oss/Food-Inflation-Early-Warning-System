"""Validate the Sentinel-2 cropland signal against REAL ground truth (V2-4).

Ground truth arrives in whatever format its publisher uses; an adapter (satellite/truth_adapters.py, written only
after looking at the real file, rule 5) turns it into the normalised schema:

    district, agri_year, crop, variable, value, unit, source
    Kolar,    2019,      Tomato, area,  12345, ha,  "Karnataka Horticulture Dept, <file name>"

agri_year Y means the Indian agricultural year July Y .. June Y+1 ("2019-20").

Satellite side, per district and agricultural year (from satellite_obs, clear-pixel weighted):
    ndvi_mean   mean cropland NDVI of all clear acquisitions in the year
    ndvi_peak   highest 10-day composite
    ndvi_amp    peak minus lowest 10-day composite (seasonality: how much crop grows and is harvested)
    n_obs       clear acquisitions behind it (clouds thin the monsoon months)

The test is WITHIN district: both sides are demeaned per district, so the question is "in years when the satellite
saw more / greener cropland, did the statistics report more area / production?", not "are big districts big".
Correlations come with n, a bootstrap 95% interval and the number of comparisons made; with few district-years the
honest outcome is often "too few points to say" (rule 11).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

METRICS = ["ndvi_mean", "ndvi_peak", "ndvi_amp"]
TRUTH_COLUMNS = ["district", "agri_year", "crop", "variable", "value", "unit", "source"]
MIN_N = 8


def agri_year(d: pd.Series) -> pd.Series:
    d = pd.to_datetime(d)
    return (d.dt.year - (d.dt.month < 7).astype(int)).astype(int)


def annual_signal(obs: pd.DataFrame, composite_days: int = 10, min_obs: int = 6) -> pd.DataFrame:
    from .features import best_view_per_day

    o = best_view_per_day(obs).dropna(subset=["ndvi_median"])
    o = o[o["clear_px"] > 0].copy()
    o["date"] = pd.to_datetime(o["date"])
    o["agri_year"] = agri_year(o["date"])
    o["bin"] = (o["date"] - pd.Timestamp("2015-01-01")).dt.days // composite_days
    o["wx"] = o["ndvi_median"] * o["clear_px"]
    comp = o.groupby(["district", "agri_year", "bin"]).agg(wx=("wx", "sum"), w=("clear_px", "sum")).reset_index()
    comp["ndvi"] = comp["wx"] / comp["w"]
    yr = o.groupby(["district", "agri_year"]).agg(wx=("wx", "sum"), w=("clear_px", "sum"), n_obs=("date", "count"),
                                                  first=("date", "min"), last=("date", "max")).reset_index()
    yr["ndvi_mean"] = yr["wx"] / yr["w"]
    c = comp.groupby(["district", "agri_year"])["ndvi"].agg(["max", "min", "count"]).reset_index()
    yr = yr.merge(c, on=["district", "agri_year"])
    yr["ndvi_peak"], yr["ndvi_amp"], yr["n_composites"] = yr["max"], yr["max"] - yr["min"], yr["count"]
    # a year seen only partly (pilot start / current year) is not comparable to a full year
    yr["complete"] = (yr["n_composites"] >= 30) & (yr["n_obs"] >= min_obs)
    return yr[["district", "agri_year", "n_obs", "n_composites", "complete", *METRICS]]


def check_truth(truth: pd.DataFrame) -> pd.DataFrame:
    missing = set(TRUTH_COLUMNS) - set(truth.columns)
    if missing:
        raise ValueError(f"ground truth needs columns {TRUTH_COLUMNS}; missing {sorted(missing)} (write an adapter)")
    t = truth.copy()
    t["agri_year"] = t["agri_year"].astype(int)
    t["value"] = pd.to_numeric(t["value"], errors="coerce")
    return t.dropna(subset=["value"])


@dataclass
class Comparison:
    crop: str
    variable: str
    metric: str
    n: int
    districts: int
    pearson_within: float | None
    spearman_within: float | None
    ci95: tuple[float, float] | None
    verdict: str


def _within(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col] - df.groupby("district")[col].transform("mean")


def _boot(x: np.ndarray, y: np.ndarray, reps: int = 2000, seed: int = 0) -> tuple[float, float] | None:
    if len(x) < 4:
        return None
    rng = np.random.default_rng(seed)
    rs = []
    for _ in range(reps):
        i = rng.integers(0, len(x), len(x))
        if np.std(x[i]) > 0 and np.std(y[i]) > 0:
            rs.append(np.corrcoef(x[i], y[i])[0, 1])
    return (round(float(np.percentile(rs, 2.5)), 3), round(float(np.percentile(rs, 97.5)), 3)) if rs else None


def compare(signal: pd.DataFrame, truth: pd.DataFrame) -> list[Comparison]:
    t = check_truth(truth)
    s = signal[signal["complete"]]
    out = []
    for (crop, var), g in t.groupby(["crop", "variable"]):
        j = s.merge(g[["district", "agri_year", "value"]], on=["district", "agri_year"])
        j = j[j.groupby("district")["agri_year"].transform("count") >= 2]  # within-district needs >= 2 years
        for m in METRICS:
            n = len(j)
            if n < 3:
                out.append(Comparison(crop, var, m, n, j["district"].nunique(), None, None, None,
                                      f"no overlap: {n} district-years with both satellite and ground truth"))
                continue
            x, y = _within(j, m).to_numpy(), _within(j, "value").to_numpy()
            p = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 and np.std(y) > 0 else None
            sp = float(pd.Series(x).rank().corr(pd.Series(y).rank())) if p is not None else None
            ci = _boot(x, y)
            if n < MIN_N:
                verdict = f"too few points to say (n = {n} < {MIN_N})"
            elif ci and (ci[0] > 0 or ci[1] < 0):
                verdict = "signal: interval excludes 0"
            else:
                verdict = "no clear signal: interval includes 0"
            out.append(Comparison(crop, var, m, n, j["district"].nunique(), None if p is None else round(p, 3),
                                  None if sp is None else round(sp, 3), ci, verdict))
    return out


def report(comps: list[Comparison]) -> pd.DataFrame:
    df = pd.DataFrame([c.__dict__ for c in comps])
    if len(df):
        df["comparisons_made"] = len(df)  # read any single "signal" against how many were tried
    return df


def main(argv=None):
    """python -m agripulse_ml.satellite.validate data/satellite/observations.csv
    -> docs/results/satellite-signal.csv (district-year NDVI) + satellite-validation.csv (comparisons). REAL data."""
    import argparse
    from pathlib import Path

    from .truth_adapters import hsg_tomato

    ap = argparse.ArgumentParser()
    ap.add_argument("observations")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "docs" / "results"))
    a = ap.parse_args(argv)
    obs = pd.read_csv(a.observations, parse_dates=["date"])
    sig = annual_signal(obs)
    rep = report(compare(sig, hsg_tomato()))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sig.assign(data_provenance="real").to_csv(out / "satellite-signal.csv", index=False)
    rep.assign(data_provenance="real").to_csv(out / "satellite-validation.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print("REAL data: Sentinel-2 L2A (Earth Search) x ESA WorldCover cropland vs Horticultural Statistics at a Glance")
        print(sig.round(3).to_string(index=False))
        print(rep.to_string(index=False))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
