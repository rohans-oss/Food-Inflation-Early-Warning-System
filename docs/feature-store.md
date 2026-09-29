# V2 feature store

`ml/agripulse_ml/features/` builds one training table per `feature_set` + `data_provenance`. Every V2 model (TFT, GNN,
ablation) reads these tables, so they all see the same rows, targets and time rules. V1's feature code is unchanged in
`features/legacy.py` and still drives the V1 LightGBM.

```bash
python -m agripulse_ml.features.build --feature-set prices+weather --provenance synthetic --seed 7
python -m agripulse_ml.features.build --feature-set prices+weather --provenance real      # from DATABASE_URL
```

Output: `data/feature_tables/<feature_set>__<provenance>[__seed<N>].parquet` plus a `.card.json` data card (rows,
date range, per-column missingness, provenance per group, lags). The synthetic table takes about 2 s to build.

## Groups

| Group | Status | Columns | Role |
|---|---|---|---|
| `prices` | built, **required** (targets are price changes) | `px_chg_{1,7,14,28}`, `px_rel_mean_{7,28}`, `px_vol_28`, `px_rel_{max,min}_28`, `px_yoy`, `px_level`, `arr_7_rel`, `arr_known` | past-only |
| `weather` | built | `wx_rain_7`, `wx_rain_30`, `wx_rain_30_anom`, `wx_tmax_7`, `wx_tmax_7_anom` | past-only |
| | | `wf_rain_next7`, `wf_rain_next14`, `wf_tmax_next7`, `wf_age_days`, `wf_available` | **known-future** |
| `calendar` | always on (not in the name) | `cal_doy_{sin,cos}`, `cal_month`, `cal_festival`, `cal_festival_next14`, `cal_season` | known-future |
| static | always | `mandi_id`, `st_district`, `st_state`, `st_lat`, `st_lon` | static covariates |
| `satellite` | built (V2-4), needs `satellite_obs` rows | `sat_ndvi_30`, `sat_obs_30`, `sat_ndvi_chg_30`, `sat_ndvi_anom`, `sat_age_days` (REAL; lag 2 days) | past-only |
| `graph` | built (V2-3) | `gr_dist_chg_{7,14}`, `gr_dist_spread`, `gr_dist_risen`, `gr_corr_chg_7`, `gr_flow_up_chg_7` (flow = ESTIMATE), `gr_n_corr` | past-only |
| `transit` | refuses until V2-5 | | |

**Graph group time rule.** The graph is rebuilt every 28 days (`config/graph.toml`), each snapshot only from prices and
arrivals published by its date; a row at t uses the latest snapshot on or before t, and its neighbours' own past-only
features at t. A mandi's graph features mix in its neighbours' prices, so its provenance is the worst of the prices
it listens to (one thin real mandi makes its neighbours `real_partial`). Leakage tests: tests/test_feature_store.py
(truncation, snapshot edges, both verified against planted leaks). Details: docs/graph-results.md.

Names are canonical: `weather+prices` → `prices+weather`. The name is stored on every run with `data_provenance`.

## Time rules (config/features.toml)

A row is an **issue date t**, the day a forecast would be made.

| Source | Publication lag | Meaning |
|---|---|---|
| Agmarknet prices | 1 day | the price dated d is usable from d+1 |
| arrivals | 1 day | |
| NASA POWER weather | 3 days | |
| Open-Meteo observed | 1 day | when both exist for a day, the sooner-published source is used |
| weather forecasts | 0 | a forecast issued on t is usable at t |

- **Base price** b(t) = last *published* price (grid price on t − 1, carried forward up to 3 days).
  V1 used the price on t itself, which was slightly optimistic.
- **Targets** y_h = log(p(t+7h) / b(t)). A label is only known on t + 7h + 1, so `table.fold_spec()` returns a
  `FoldSpec(label_lag_days=1)`, and walk-forward training never uses a label published after the fold cutoff.
- **Known-future weather** comes from the latest forecast issued on or before t (up to 3 days old; `wf_age_days` says
  how old). Forecasts cover leads 1–16 days, so 3- and 4-week horizons get only the first 14 days of forecast.
- `build_table(..., as_of=day)` extends issue dates to a given "today", so a forecast can be made after a day with no price.

## Weather forecasts: real vs synthetic

- **Real:** the `weather` table keeps only the latest forecast and overwrites it hourly, so past forecasts were being
  lost. From V2-1 on, the hourly Open-Meteo job also writes every forecast to `weather_forecasts`, keyed by the IST day
  it was issued (the last fetch of the day wins). **Real known-future weather only becomes usable in backtests once
  this archive covers the training window: about 13 months, like prices.** The readiness monitor tracks it
  ("Forecast archive" column on Admin).
- **Synthetic:** forecasts are *simulated* as the future synthetic observation plus error that grows with lead time
  (`[synthetic_forecast]` in the config). That uses future synthetic values by construction, which is what a simulated
  forecast is. It is labelled synthetic and never used with real data.

## Leakage tests (tests/test_feature_store.py)

| Test | Proves |
|---|---|
| future deleted, per group (prices, weather-past, weather-forecast, calendar) | building "as of C+1" with all data after C removed changes no feature at any issue date ≤ C+1 |
| same-day price | changing the price *on* t changes nothing at t (and does change t+1) |
| NASA POWER 3-day lag | NASA values from t−1 and t−2 don't reach t; t−3 does; Open-Meteo's t−1 does |
| forecasts issued after t | with no forecast on t, features come from t−1, never from t+1 |
| archive gaps | a forecast ≤ 3 days old is used with its age; older → missing |
| folds | every training label + 1-day lag is before the fold cutoff |

**The tests were checked against planted leaks.** Setting the price lag to 0 fails 3 tests; letting forecasts be
looked up forward in time fails 2. Both were reverted.

## Data card: synthetic `prices+weather`, seed 7 (SYNTHETIC — METHODOLOGY DEMO)

31,089 rows, 18 mandis, 2022-01-02 → 2026-09-26, 32 features (5 static, 11 known-future, 18 past-only).
Target coverage 98–99.5%, spike rate 24.7%. Most-missing columns: `px_yoy` 21% (needs a year of history), `px_level` 3%,
everything else under 2%.
