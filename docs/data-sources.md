# Data sources (V1)

"Real time" means something different for each source. Say it this precisely in any demo.

| Source | Freshness | Key | Used for | Code |
|---|---|---|---|---|
| Agmarknet via data.gov.in | Daily, current day only | Yes (free) | Mandi min / max / modal prices | `services/ingest/ingest/agmarknet.py` |
| Open-Meteo forecast API | Hourly model runs, daily aggregates | No | Rain, Tmax/Tmin, humidity (last 7 days + 16-day forecast) | `services/ingest/ingest/weather.py` |
| NASA POWER daily point | Daily, a few days' lag | No | Historical rain, temperature, RH, solar radiation | `services/ingest/ingest/weather.py` |
| OpenStreetMap + OSRM | Static road graph | No | Route, road distance, ETA | `services/tracking/tracking/routing.py` |
| Driver phone GPS | Every 5–10 s during a trip | — | Live vehicle position | `services/tracking/tracking/` |

## Agmarknet (data.gov.in)

What was verified, and how (rule 5 — nothing here is guessed):

- **Endpoint:** `GET https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070`
  ("Current Daily Price of Various Commodities from Various Markets (Mandi)").
  Params: `api-key`, `format=json`, `limit`, `offset`, `filters[state]`, `filters[commodity]`.
- **Response:** `{"records": [...], "total": N, ...}`. The resource id, parameter names, and record
  keys were checked against working open-source clients of the same resource and a real payload
  dated 25/09/2026 (18 Karnataka tomato rows are stored as `tests/fixtures/agmarknet_ka_tomato_2026-09-25.json`).
- **Record keys:** `state, district, market, commodity, variety, grade, arrival_date, min_price, max_price, modal_price`.
  `arrival_date` is `DD/MM/YYYY`. Prices are **Rs per quintal**.
- **No arrival tonnage** in this resource. Arrivals come from bulk report downloads
  (`python -m ingest.run backfill file.csv`) and from traders confirming deliveries in the app.
- **Only the current day is served**, and `limit` is capped per request, so the job pages until
  `total` and archives every raw payload under `data/raw/agmarknet/`. Start the scheduler on day 1:
  your own daily pull *is* your history.
- The real feed contains bad rows. The 25/09/2026 Belgaum APMC tomato row has `min_price 2500 > max_price 2000`
  and a modal outside both. The cleaning layer flags these instead of dropping them silently.

**Still to verify with your own key:** the exact response wrapper keys beyond `records` / `total`, the
per-request limit, and the daily quota. Run `python -m ingest.run agmarknet` once and read
`data/raw/agmarknet/<date>.json`.

### Bulk history (backfill)

`python -m ingest.run backfill <file> [--state Karnataka]` accepts:

- a saved JSON payload (`{"records": [...]}` or a bare list), or
- a CSV report download. Known header spellings are mapped in `CSV_ALIASES`; an unknown layout
  fails with the headers it saw, so you add the mapping after looking at a real file.

## Cleaning rules (`services/ingest/ingest/cleaning.py`)

| Flag | Rule | Outlier? |
|---|---|---|
| `min_gt_max` | min > max | only together with another flag |
| `modal_outside` | modal not within [min, max] | yes |
| `non_positive` | modal ≤ 0 | yes |
| `jump_vs_history` | modal > 5× above or below the mandi's trailing 30-day median | yes |

The jump rule is deliberately loose. Tomato legitimately doubles within weeks, and those moves are
the thing being forecast. It only catches unit and typo errors.

Mandi names are matched on a normalised key (case, punctuation, spacing, "APMC" suffix ignored).
Unknown markets are created automatically **without coordinates**; they stay off maps and out of
the recommender until an admin sets and verifies their location (`PATCH /admin/mandis/{id}`).
Seeded coordinates are approximate town centroids with `coords_verified = false`.

## Weather

- **Open-Meteo** `https://api.open-meteo.com/v1/forecast` with
  `daily=precipitation_sum,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean`,
  `timezone=Asia/Kolkata`, `past_days`, `forecast_days`. Response `daily.time[]` + one array per variable.
  Days after today are stored with `is_forecast = true` and are never used as observed features.
- **NASA POWER** `https://power.larc.nasa.gov/api/temporal/daily/point` with
  `parameters=T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN&community=AG&format=JSON`,
  `start` / `end` as `YYYYMMDD`. Response `properties.parameter.<NAME>.<YYYYMMDD>`. `-999` fill values are stored as NULL.
- For model features, NASA POWER is preferred for history, and Open-Meteo fills the recent days NASA hasn't published yet.
- **Forecast archive (V2-1):** every Open-Meteo run also stores its future days in `weather_forecasts`, keyed by the IST
  issue date (the day's last fetch wins). This is the only record of what the forecast said on past days, which
  backtests of known-future weather need. It starts empty; see `docs/feature-store.md`.

## Synthetic data

`python -m ingest.run synthetic` writes a clearly fake history (`source = 'synthetic'`) so the pipeline
can be developed and demoed before real history exists. Synthetic and real rows are **never mixed**
in one model. A model trained on synthetic data is stored with `trained_on_synthetic = true`, and every
API response and screen that shows it carries a "Synthetic" badge.
