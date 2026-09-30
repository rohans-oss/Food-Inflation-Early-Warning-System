# V2-4 results: Sentinel-2 cropland signal vs reported tomato area / production

> **REAL DATA (`data_provenance = real`).** Sentinel-2 L2A via Earth Search, ESA WorldCover 2021 cropland, and the
> tomato statistics printed in *Horticultural Statistics at a Glance* (2018, 2021 and 2024 editions).
> Pilot: 2 districts, 2018 to 2026. This is a small sample and is reported as such.

## Bottom line

**District-wide cropland greenness shows no evidence of tracking tomato area or production from year to year.**
Of 6 pre-planned comparisons, one clears the 95% bar: tomato area vs mean cropland NDVI, within-district r = 0.60,
95% interval 0.14 to 0.89. It disappears once a shared time trend is removed (r = −0.14). Both series rise from
2018-19 (a drought year) to 2022-23: NDVI correlates 0.97 with the year within district, and area 0.65. The
correlation reflects the trend they share, not greenness tracking tomato area. With 6 comparisons, one passing by
chance is not surprising either.

This matches the expectation stated before the run. Reported tomato area (Kolar 13–20 thousand ha) is roughly 8% of
the WorldCover cropland inside each 30 km circle, so a district-wide NDVI is dominated by other crops (finger
millet, groundnut, fodder, mulberry and others). A tomato signal would need a tomato-specific mask, field
boundaries, or crop-type classification. That is a design change for later, not a tweak to try now (rule 11).

What *is* established:
- **The pipeline produces a physically sensible, real crop signal.** NDVI stays within 0.15–0.88. The seasonal
  cycle is right for Karnataka: lowest in March–May (median ~0.27), highest after the monsoon in October–November
  (~0.51). 2019 is the low year, consistent with the 2018-19 drought in the Kolar belt.
- **That signal is usable as a feature.** The `satellite` feature group built from these observations covers 96–97%
  of issue dates for mandis in the two districts, with provenance `real`.

## The comparisons

Within-district correlation of each satellite metric with the reported value, per district and agricultural year
(July–June). Both sides are demeaned per district. The interval is a 2000-draw bootstrap. "Detrended" repeats the
correlation after removing a linear time trend from both sides.

**Year rule used for the result** (a year counts when clear views exist within 31 days of both ends of the year):
n = 10 district-years (2018-19 to 2022-23 in both districts).

| reported | satellite metric | n | r within | 95% interval | r detrended | verdict |
|---|---|---|---|---|---|---|
| area | mean NDVI | 10 | 0.60 | 0.14 to 0.89 | **−0.14** | interval excludes 0, but it is the shared trend |
| area | peak 10-day NDVI | 10 | 0.70 | −0.10 to 0.99 | 0.52 | no clear signal |
| area | seasonal amplitude | 10 | 0.43 | −0.63 to 0.93 | 0.48 | no clear signal |
| production | mean NDVI | 10 | 0.35 | −0.36 to 0.93 | 0.04 | no clear signal |
| production | peak 10-day NDVI | 10 | 0.36 | −0.76 to 0.82 | 0.20 | no clear signal |
| production | seasonal amplitude | 10 | 0.20 | −0.66 to 0.72 | 0.18 | no clear signal |

**The pre-registered year rule** (≥ 30 of 36 ten-day windows with a clear view) gives n = 5, and every comparison
reads "too few points to say". That rule was set before any real data. On the real pilot, monsoon cloud leaves only
23–33 clear windows even in full years, so it discarded full years as well as partial ones. The rule above follows
the original intent (exclude the partial first and current years) and was adopted **after** seeing the data. Both
are reported, and all numbers for both are in `docs/results/satellite-validation.csv`.

Why 2023-24 is missing: in both districts it has no clear view within a month of one end of the year (monsoon
cloud), so the coverage rule drops it. The raw 0.60 is stable when any single district-year is removed
(0.47–0.71), which is further evidence that it comes from the trend rather than from one odd year.

## Data

| | |
|---|---|
| Scenes | Earth Search `sentinel-2-l2a`, 2018-01-04 to 2026-09-29, scene cloud ≤ 60%. Kolar 1,103 scenes, Chikkaballapur 1,508 (the circles straddle 2–3 tiles). 2,610 rows, 2,569 with NDVI; the rest were too cloudy inside the district |
| Per row | NDVI median / mean / quartiles over clear cropland pixels at 80 m, clear fraction, scene and tile ids, processing baseline, the offset used |
| District area | 30 km circle around the district HQ (**approximation**; real district polygons are a later improvement) |
| Ground truth | `data/ground_truth/tomato_district_hsg.csv`: Kolar + Chikballapur, 2015-16 to 2023-24, edition / table / page per row (docs/data-sources.md) |
| Files | `data/satellite/observations.csv` (the pilot output), `docs/results/satellite-signal.csv` (district-year metrics), `docs/results/satellite-validation.csv` (all comparisons, both year rules) |

## Problems found on the real data (all fixed and tested)

1. **Some catalogue items have no band assets** (`S2A_43PGQ_20200119_1_L2A`): they are skipped and logged, where
   they had crashed the dry run.
2. **Old scenes are listed twice** (original `_0` and reprocessed `_1`): only the reprocessed one is kept. 295 + 402
   duplicates were dropped.
3. **Overlapping tiles see the same fields on the same day:** features and validation keep the most complete view
   per district and day.
4. **The offset was applied twice.** Items flagged `earthsearch:boa_offset_applied: true` still list a −0.1 offset,
   but their pixels are already corrected. This was verified on raw values of the same tile and day (red median 768
   vs 773). The first full run produced NDVI up to 1.93. Now the flag is honoured, negative reflectance is dropped,
   and any NDVI median outside [−1, 1] is refused. 1,911 rows were re-fetched.
5. **144 reads failed during a network outage** on the fetching machine; the resumable runner retried them.

## Compute

Run on the user's Windows PC (the build workspace cannot reach these hosts). The first pass took 2,605 reads,
including a network outage. The re-fetch took 2,056 reads in 2.2 hours, **3.9–4.8 s per read**. Measured data volume
was not logged. The earlier planning estimate was 7.6 GB.

## Reproduce

```bash
pip install -e .[satellite]
python -m agripulse_ml.satellite.run --out data/satellite          # ~3 h for the 2-district pilot; resumable
python -m agripulse_ml.satellite.validate data/satellite/observations.csv
python -m agripulse_ml.satellite.load data/satellite/observations.csv    # into satellite_obs
```
