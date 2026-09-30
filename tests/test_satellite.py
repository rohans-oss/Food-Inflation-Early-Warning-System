"""V2-4 Sentinel-2 pipeline on small local fixtures shaped like the real sources (no network in tests):
STAC paging + cloud filter, per-scene reflectance offset, SCL + cropland masking, resumable runs, loading,
the `satellite` feature group's publication lag and leakage, and the validation statistics."""
import copy
import dataclasses
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from agripulse_api.models import SatelliteObs
from agripulse_ml.features.inputs import Inputs
from agripulse_ml.features.store import build_table
from agripulse_ml.satellite.config import satellite_config
from agripulse_ml.satellite.features import SATELLITE, district_series
from agripulse_ml.satellite.load import load_csv
from agripulse_ml.satellite.validate import annual_signal, compare, report

KOLAR = {"name": "Kolar", "lat": 13.137, "lon": 78.129, "radius_km": 3}
STAC = "https://stac.test/v1"


# ---------------------------------------------------------------- fixtures: COGs like Earth Search / WorldCover


def _write(path, arr, transform, crs, nodata=0):
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype=arr.dtype,
                       crs=crs, transform=transform, nodata=nodata, tiled=True, blockxsize=256, blockysize=256) as d:
        d.write(arr, 1)
        d.build_overviews([2, 4, 8], Resampling.average)


@pytest.fixture(scope="module")
def cogs(tmp_path_factory):
    pytest.importorskip("rasterio")
    from pyproj import Transformer
    from rasterio.transform import from_origin

    root = tmp_path_factory.mktemp("s2")
    x, y = Transformer.from_crs(4326, 32643, always_xy=True).transform(KOLAR["lon"], KOLAR["lat"])
    half = 5000
    ox, oy = round(x - half, -1), round(y + half, -1)
    n10, n20 = 1000, 500
    red = np.full((n10, n10), 1500, np.uint16)   # reflectance 0.05 with offset -0.1, 0.15 without
    nir = np.full((n10, n10), 4500, np.uint16)   # reflectance 0.35 with offset -0.1, 0.45 without
    scl = np.full((n20, n20), 4, np.uint8)       # vegetation ...
    scl[n20 // 2:, :] = 9                        # ... except the southern half: cloud (dropped)
    _write(root / "B04.tif", red, from_origin(ox, oy, 10, 10), "EPSG:32643")
    _write(root / "B08.tif", nir, from_origin(ox, oy, 10, 10), "EPSG:32643")
    _write(root / "SCL.tif", scl, from_origin(ox, oy, 20, 20), "EPSG:32643")
    # WorldCover tile N12E078 (EPSG:4326): cropland (40) west of the HQ, tree cover (10) east of it
    res = 1 / 12000
    w = int(0.2 / res)
    lc = np.full((w, w), 10, np.uint8)
    lc[:, : w // 2] = 40
    _write(root / "WC_N12E078.tif", lc, from_origin(KOLAR["lon"] - 0.1, KOLAR["lat"] + 0.1, res, res), "EPSG:4326")
    return root


def _cfg(root):
    cfg = copy.deepcopy(satellite_config())
    cfg["stac"]["url"] = STAC
    cfg["cropland"]["url_template"] = str(root / "WC_{tile}.tif")
    cfg["extract"]["min_valid_pixels"] = 10
    return cfg


def _feature(root, sid, day, cloud, offset):
    band = lambda o: {"raster:bands": [{"scale": 0.0001, "offset": o, "nodata": 0}]}  # noqa: E731
    return {"type": "Feature", "id": sid,
            "properties": {"datetime": f"{day}T05:25:18Z", "eo:cloud_cover": cloud, "proj:epsg": 32643,
                           "grid:code": "MGRS-43PHQ", "s2:processing_baseline": "05.12" if offset else "02.14"},
            "assets": {"red": {"href": str(root / "B04.tif"), **band(offset)},
                       "nir": {"href": str(root / "B08.tif"), **band(offset)},
                       "scl": {"href": str(root / "SCL.tif"), **band(0.0)}}}


def _mock_stac(root):
    page1 = {"type": "FeatureCollection", "features": [_feature(root, "S2A_OLD", "2021-03-01", 5.0, 0.0),
                                                       _feature(root, "S2C_NEW", "2026-03-28", 1.2, -0.1)],
             "links": [{"rel": "next", "href": f"{STAC}/search?page=2"}]}
    page2 = {"type": "FeatureCollection", "features": [_feature(root, "S2B_CLOUDY", "2026-04-02", 95.0, -0.1)], "links": []}
    respx.get(f"{STAC}/search", params={"page": "2"}).mock(return_value=httpx.Response(200, json=page2))
    respx.get(f"{STAC}/search").mock(return_value=httpx.Response(200, json=page1))


# ---------------------------------------------------------------- search + extract


@respx.mock
def test_search_pages_filters_clouds_and_reads_per_scene_offsets(cogs):
    from agripulse_ml.satellite.run import bbox_of
    from agripulse_ml.satellite.stac import search

    _mock_stac(cogs)
    scenes = search(bbox_of(KOLAR), "2021-01-01", "2026-09-29", _cfg(cogs))
    assert [s.id for s in scenes] == ["S2A_OLD", "S2C_NEW"]  # cloudy scene dropped, oldest first
    assert scenes[0].offset["red"] == 0.0 and scenes[1].offset["red"] == -0.1 and scenes[1].epsg == 32643


@respx.mock
def test_search_skips_items_without_assets_and_keeps_the_reprocessed_duplicate(tmp_path):
    """Both seen on the first real pilot run: an item without the red asset crashed the dry run, and 2018 scenes
    come twice (_0_L2A baseline 00.01 and the reprocessed _1_L2A baseline 05.00, same tile and time)."""
    from agripulse_ml.satellite.stac import search

    old = _feature(tmp_path, "S2B_43PHQ_20180213_0_L2A", "2018-02-13", 5.0, 0.0)
    new = _feature(tmp_path, "S2B_43PHQ_20180213_1_L2A", "2018-02-13", 5.0, 0.0)
    new["properties"]["s2:processing_baseline"] = "05.00"
    broken = _feature(tmp_path, "S2A_43PHQ_20180220_0_L2A", "2018-02-20", 3.0, 0.0)
    del broken["assets"]["red"]
    respx.get(f"{STAC}/search").mock(return_value=httpx.Response(
        200, json={"type": "FeatureCollection", "features": [old, broken, new], "links": []}))
    skipped, logs = [], []
    scenes = search((78.0, 13.0, 78.3, 13.3), "2018-01-01", "2018-03-01", _cfg(tmp_path), log=logs.append, skipped=skipped)
    assert [x.id for x in scenes] == ["S2B_43PHQ_20180213_1_L2A"]
    assert len(skipped) == 1 and "S2A_43PHQ_20180220_0_L2A" in skipped[0] and "red" in skipped[0]
    assert any("duplicate" in m for m in logs) and any("skipped 1" in m for m in logs)


def test_extract_masks_cloud_and_non_cropland_and_applies_offset(cogs):
    from agripulse_ml.satellite.extract import district_stats
    from agripulse_ml.satellite.stac import parse

    cfg = _cfg(cogs)
    new = district_stats(parse(_feature(cogs, "S2C_NEW", "2026-03-28", 1.2, -0.1)), KOLAR, cfg)
    old = district_stats(parse(_feature(cogs, "S2A_OLD", "2021-03-01", 5.0, 0.0)), KOLAR, cfg)
    assert new["ndvi_median"] == pytest.approx(0.75, abs=1e-3)   # (0.35 - 0.05) / (0.35 + 0.05)
    assert old["ndvi_median"] == pytest.approx(0.50, abs=1e-3)   # (0.45 - 0.15) / (0.45 + 0.15)
    # only the cropland half of the circle counts, and the cloudy half of that is dropped
    circle_px = np.pi * (3000 / 80) ** 2
    assert 0.4 * circle_px < new["cropland_px"] < 0.6 * circle_px
    assert new["clear_frac"] == pytest.approx(0.5, abs=0.08)


@respx.mock
def test_run_is_resumable_and_loads_idempotently(cogs, tmp_path, db):
    from agripulse_ml.satellite.run import run

    _mock_stac(cogs)
    cfg = _cfg(cogs)
    est = run(tmp_path, "2021-01-01", "2026-09-29", [KOLAR], cfg, dry_run=True, log=lambda *a: None)
    assert est["scenes_to_fetch"] == 2 and not (tmp_path / "observations.csv").exists()
    out = run(tmp_path, "2021-01-01", "2026-09-29", [KOLAR], cfg, log=lambda *a: None)
    assert out["written"] == 2 and out["failed"] == 0
    again = run(tmp_path, "2021-01-01", "2026-09-29", [KOLAR], cfg, log=lambda *a: None)
    assert again["written"] == 0 and again["scenes_to_fetch"] == 0
    csv = str(tmp_path / "observations.csv")
    assert load_csv(db, csv)["added"] == 2 and load_csv(db, csv)["updated"] == 2
    assert db.query(SatelliteObs).count() == 2
    assert {o.scene_id: o.ndvi_median for o in db.query(SatelliteObs)} == pytest.approx({"S2A_OLD": 0.5, "S2C_NEW": 0.75}, abs=1e-3)


# ---------------------------------------------------------------- feature group: lag + leakage


def _obs(start="2023-01-01", end="2025-03-31", every=5, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.date_range(start, end, freq=f"{every}D")
    doy = d.dayofyear.to_numpy()
    nd = 0.4 + 0.2 * np.sin(doy / 365 * 2 * np.pi) + rng.normal(0, 0.03, len(d))
    return pd.DataFrame({"district": "Kolar", "date": d, "ndvi_median": nd, "clear_px": rng.integers(50, 500, len(d))})


def test_acquisition_is_usable_only_after_the_publication_lag():
    obs = _obs()
    idx = pd.date_range("2024-01-01", "2024-12-31")
    base = district_series(obs, idx, lag=2)
    d = obs.loc[obs["date"] >= "2024-06-10", "date"].iloc[0]  # an actual acquisition day
    bumped = obs.copy()
    bumped.loc[bumped["date"] == d, "ndvi_median"] += 0.5
    b = district_series(bumped, idx, lag=2)
    pd.testing.assert_frame_equal(base.loc[:d + pd.Timedelta(days=1)], b.loc[:d + pd.Timedelta(days=1)])  # d, d+1: unseen
    assert b.loc[d + pd.Timedelta(days=2), "sat_ndvi_30"] > base.loc[d + pd.Timedelta(days=2), "sat_ndvi_30"]  # d+2: seen


@pytest.fixture(scope="module")
def sat_inputs():
    inp = Inputs.from_synthetic(seed=11, start=date(2023, 1, 1), end=date(2025, 3, 31), n_mandis=3)
    return dataclasses.replace(inp, satellite=_obs())


def test_satellite_features_unchanged_when_the_future_is_deleted(sat_inputs):
    full = build_table(sat_inputs, "prices+satellite")
    # cutoff the day before an acquisition, so a lag bug (using day C+1's scene on C+1) would show
    C = sat_inputs.satellite.loc[sat_inputs.satellite["date"] >= "2024-08-20", "date"].iloc[0] - pd.Timedelta(days=1)
    cut = dataclasses.replace(sat_inputs, prices=sat_inputs.prices[sat_inputs.prices["date"] <= C],
                              arrivals=sat_inputs.arrivals[sat_inputs.arrivals["date"] <= C],
                              satellite=sat_inputs.satellite[sat_inputs.satellite["date"] <= C])
    part = build_table(cut, "prices+satellite", as_of=C + pd.Timedelta(days=1))
    key = ["mandi_id", "date"]
    win = full.df[(full.df["date"] > C - pd.Timedelta(days=60)) & (full.df["date"] <= C + pd.Timedelta(days=1))]
    kolar = [m for m, d in zip(sat_inputs.mandis["mandi_id"], sat_inputs.mandis["district"]) if d == "Kolar"]
    j = win[key + SATELLITE].merge(part.df[key + SATELLITE], on=key, suffixes=("", "_cut"))
    j = j[j["mandi_id"].isin(kolar)]  # the only mandi in a covered district
    assert len(j) > 50 and j["sat_ndvi_30"].notna().mean() > 0.9
    for c in SATELLITE:
        np.testing.assert_allclose(j[c].to_numpy(float), j[f"{c}_cut"].to_numpy(float), equal_nan=True, err_msg=c)


def test_satellite_group_is_real_and_only_covers_matched_districts(sat_inputs):
    tb = build_table(sat_inputs, "prices+satellite")
    assert tb.group_provenance["satellite"] == "real" and tb.data_provenance == "synthetic"  # prices still synthetic
    districts = dict(zip(sat_inputs.mandis["mandi_id"], sat_inputs.mandis["district"]))
    covered = tb.df.groupby("mandi_id")["sat_ndvi_30"].apply(lambda s: s.notna().any())
    assert all(covered[m] == (districts[m] == "Kolar") for m in covered.index)
    assert "sat_ndvi_anom" in tb.feature_columns and tb.df["sat_ndvi_anom"].notna().any()
    with pytest.raises(ValueError, match="Sentinel-2"):
        build_table(dataclasses.replace(sat_inputs, satellite=sat_inputs.satellite.iloc[0:0]), "prices+satellite")


# ---------------------------------------------------------------- validation statistics (fabricated truth, test only)


def test_validation_is_within_district_and_says_when_n_is_too_small():
    obs = pd.concat([_obs("2018-07-01", "2025-06-30", seed=s).assign(district=d) for s, d in ((1, "Kolar"), (2, "Chikkaballapur"))])
    sig = annual_signal(obs)
    assert sig["complete"].sum() == 14 and set(sig["district"]) == {"Kolar", "Chikkaballapur"}
    s = sig.set_index(["district", "agri_year"])
    # FABRICATED truth for the test: area follows the satellite peak within district, plus a big district offset
    truth = pd.DataFrame([{"district": d, "agri_year": y, "crop": "Tomato", "variable": "area",
                           "value": (5000 if d == "Kolar" else 500) + 20000 * s.loc[(d, y), "ndvi_peak"],
                           "unit": "ha", "source": "test"} for d, y in s.index])
    rep = report(compare(sig, truth))
    peak = rep[rep["metric"] == "ndvi_peak"].iloc[0]
    assert peak["n"] == 14 and peak["pearson_within"] == pytest.approx(1.0) and peak["verdict"].startswith("signal")
    assert (rep["comparisons_made"] == 3).all()
    few = report(compare(sig, truth[truth["agri_year"] >= 2022]))
    assert few["verdict"].str.startswith("too few").all()
    with pytest.raises(ValueError, match="adapter"):
        compare(sig, truth.drop(columns=["unit"]))


def test_overlapping_tiles_count_once_per_day():
    """Real pilot rows, 2018-01-04 Kolar: three overlapping tiles saw the same fields."""
    from agripulse_ml.satellite.features import best_view_per_day

    rows = pd.DataFrame({"district": "Kolar", "date": pd.Timestamp("2018-01-04"),
                         "scene_id": ["S2B_43PGQ_20180104_0_L2A", "S2B_43PHQ_20180104_0_L2A", "S2B_44PKV_20180104_0_L2A"],
                         "in_scene_px": [330, 317677, 78818], "clear_px": [324, 263264, 69207],
                         "ndvi_median": [0.404, 0.3944, 0.3999]})
    best = best_view_per_day(rows)
    assert best["scene_id"].tolist() == ["S2B_43PHQ_20180104_0_L2A"]
    s = district_series(rows, pd.date_range("2018-01-01", "2018-01-10"), lag=2)
    assert s.loc["2018-01-06", "sat_obs_30"] == 1 and s.loc["2018-01-06", "sat_ndvi_30"] == pytest.approx(0.3944)


def test_hsg_tomato_ground_truth_adapter():
    """The real published figures (data/ground_truth/tomato_district_hsg.csv), checked against the printed tables."""
    from agripulse_ml.satellite.truth_adapters import hsg_tomato
    from agripulse_ml.satellite.validate import check_truth

    t = check_truth(hsg_tomato())
    assert len(t) == 36 and set(t["district"]) == {"Kolar", "Chikkaballapur"}
    area = t[t["variable"] == "area"].set_index(["district", "agri_year"])["value"]
    # HSG 2018 prints 2016-17 Kolar as 8.51 thousand ha; HSG 2021 prints 8510 (ha): same number, units reconciled
    assert area[("Kolar", 2016)] == 8510 and area[("Kolar", 2015)] == 5960
    assert area[("Chikkaballapur", 2023)] == 10879 and t["agri_year"].between(2015, 2023).all()
    assert t["source"].str.contains("Table 7.4.1|Table 7.5.32").all()


def test_offset_is_not_applied_twice_when_the_provider_already_removed_it(cogs):
    """Real finding (2026-09-30): Earth Search items with earthsearch:boa_offset_applied = true still list
    raster:bands offset -0.1, but their pixels already had the +1000 removed (verified on raw DNs:
    S2B_43PHQ_20191105_0 vs _1, red median 768 vs 773). Applying -0.1 again gave NDVI > 1."""
    from agripulse_ml.satellite.extract import district_stats
    from agripulse_ml.satellite.stac import parse

    f = _feature(cogs, "S2B_43PHQ_20191105_1_L2A", "2019-11-05", 2.0, -0.1)
    f["properties"]["earthsearch:boa_offset_applied"] = True
    sc = parse(f)
    assert sc.offset == {"red": 0.0, "nir": 0.0, "scl": 0.0} and sc.boa_offset_applied is True
    row = district_stats(sc, KOLAR, _cfg(cogs))
    assert row["ndvi_median"] == pytest.approx(0.50, abs=1e-3)  # 0.15 / 0.45 reflectance, no second offset
    assert row["offset_red"] == 0.0 and row["pipeline_version"] == 2
    g = _feature(cogs, "S2B_43PHQ_20191105_0_L2A", "2019-11-05", 2.0, -0.1)
    g["properties"]["earthsearch:boa_offset_applied"] = False
    assert parse(g).offset["red"] == -0.1  # flag false: the listed offset is real and applied


def test_impossible_ndvi_is_refused_not_stored(cogs):
    """If a wrong offset makes reflectance negative, those pixels are dropped; nothing outside [-1, 1] is stored."""
    from agripulse_ml.satellite.extract import district_stats
    from agripulse_ml.satellite.stac import parse

    f = _feature(cogs, "S2B_43PHQ_20220301_0_L2A", "2022-03-01", 2.0, -0.2)  # red 0.15 - 0.2 < 0 everywhere
    row = district_stats(parse(f), KOLAR, _cfg(cogs))
    assert row["clear_px"] == 0 and row["ndvi_median"] is None


def test_version_1_file_keeps_only_old_baseline_rows(tmp_path):
    from agripulse_ml.satellite.run import BASE_FIELDS, FIELDS, migrate_v1

    p = tmp_path / "observations.csv"
    rows = [dict.fromkeys(BASE_FIELDS, "1") | {"scene_id": f"S{i}", "district": "Kolar", "baseline": b}
            for i, b in enumerate(["00.01", "02.14", "03.01", "04.00", "05.00", "05.11"])]
    with open(p, "w", newline="") as f:
        import csv as _csv

        w = _csv.DictWriter(f, fieldnames=BASE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    out = migrate_v1(p, log=lambda *a: None)
    assert out == {"kept": 3, "redo": 3} and (tmp_path / "observations.v1.csv").exists()
    kept = pd.read_csv(p, dtype={"baseline": str})
    assert list(kept.columns) == FIELDS and kept["baseline"].tolist() == ["00.01", "02.14", "03.01"]
    assert migrate_v1(p, log=lambda *a: None) is None  # already version 2: untouched
