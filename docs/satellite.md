# V2-4: Sentinel-2 crop signal (REAL data)

Status: **done for the 2-district pilot.** Result (REAL data): [docs/satellite-results.md](satellite-results.md).
District-wide cropland NDVI shows no evidence of tracking tomato area or production beyond a shared time trend,
but it is a physically sensible, seasonal crop signal and is available as the `satellite` feature group.

## What it measures

For each Sentinel-2 L2A scene and each pilot district (Kolar, Chikkaballapur):

1. District area = a 30 km circle around the district HQ. **Approximation**: real district polygons replace it later.
2. Cropland only: ESA WorldCover 2021 class 40.
3. Clear pixels only: scene classification (SCL) 4 = vegetation, 5 = not vegetated. Cloud, cloud shadow, cirrus,
   water, dark and unclassified pixels are dropped.
4. Reflectance = DN × scale + offset, **read per scene** (−0.1 offset since processing baseline 04.00, 2022).
5. NDVI = (NIR − red) / (NIR + red), read at 80 m from the COG overviews. That's ~64× less data than 10 m, which is
   enough for a district-level signal and too coarse for single fields.

Output: one row per (scene, district) with NDVI median / mean / quartiles and how many cropland pixels were clear.
Too-cloudy rows are kept with an empty NDVI, so gaps are visible and the run can resume.

## Running the pilot (on your machine)

```bash
pip install -e .[satellite]                       # rasterio + pyproj (Windows wheels exist)
python -m agripulse_ml.satellite.run --out data/satellite --dry-run      # count scenes, estimate GB and hours
python -m agripulse_ml.satellite.run --out data/satellite --limit 3      # smoke test: 3 scenes per district
python -m agripulse_ml.satellite.run --out data/satellite                # full pilot; re-run to resume after a stop
python -m agripulse_ml.satellite.load data/satellite/observations.csv   # into the database
```

The pilot needs these hosts: `earth-search.aws.element84.com`, `sentinel-cogs.s3.us-west-2.amazonaws.com` and
`esa-worldcover.s3.eu-central-1.amazonaws.com`.

**Compute:** the dry run prints the real scene count. The per-scene cost behind its GB / hours figure is an
ESTIMATE (~3 MB and ~4 s per scene per district) until the smoke test measures it. The earlier planning figure was
1–3 h and ~5 GB for the 2-district pilot. Run the smoke test first and check its timing before starting the full run.

## Feature group `satellite` (past-only, REAL)

`sat_ndvi_30`, `sat_obs_30`, `sat_ndvi_chg_30`, `sat_ndvi_anom` (vs the same day in prior years only), `sat_age_days`.
An acquisition dated d is usable from d + 2 (config/features.toml). Mandis in districts without satellite data get
NaN. Leakage tests: tests/test_satellite.py (lag and truncation, both checked against a planted zero-lag leak).

## Validation

`satellite/validate.py` compares per district and agricultural year (July–June):

- satellite side: mean NDVI, peak 10-day composite, seasonal amplitude
- ground-truth side: whatever the files hold (tomato area / production; ICRISAT crops if a post-2015 file exists)

The comparison is **within district** (both sides demeaned per district), with n, a bootstrap 95% interval, and
the number of comparisons tried. With 2 districts and about 7 complete years (n ≈ 14 at best), "too few points to
say" is a likely and acceptable outcome.

Why not the ICRISAT apportioned file: it ends in 2011-12 and Sentinel-2 starts in 2015, and it has no tomato
(docs/data-sources.md).
