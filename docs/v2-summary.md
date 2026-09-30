# V2 (Intelligence): summary

V2 set out to add better models (TFT, a mandi graph + GNN) and new data (Sentinel-2, in-transit tonnage) to the V1
tomato forecaster, and to measure each against simple baselines honestly. Real price history only started on
2026-09-25, so every **price** result below is on synthetic data and says so. The satellite study is on real data.

## What V2 found

| phase | study | data | result |
|---|---|---|---|
| V2-0 | shared evaluation harness, readiness monitor, provenance labels | SYNTHETIC | V1's "LightGBM beats naive" held on **one** synthetic draw; across 8 draws there is no reliable edge. Every later comparison uses several draws for that reason |
| V2-1 | feature store | — | 5 groups (prices, weather, satellite, graph, transit) + calendar, publication lags, leakage tests checked against planted leaks |
| V2-2 | Temporal Fusion Transformer | SYNTHETIC | **Negative.** 15–33% worse than naive; raw intervals 46–59% coverage (target 80%). Not tuned (rule 11) |
| V2-3 | mandi graph + GNN | SYNTHETIC (distance edges real) | **Mixed.** Graph *features* help LightGBM 1–3% on every draw, but only to parity with naive; the GNN loses 10–32% and is unstable. The positive control (planted spatial signal) is a weak pass |
| V2-4 | Sentinel-2 crop signal vs tomato statistics | **REAL** | **Negative for tomato.** District cropland NDVI does not track tomato area or production beyond a shared trend (r 0.60 → −0.14 detrended; 2 districts, n = 10). The NDVI signal itself is sound and seasonal |
| V2-5 | transit feature + ablation | SYNTHETIC; real run: not enough data | No group makes LightGBM reliably beat naive. The transit gain is built in by simulation, and the satellite gain is seasonality. Removing weather helped in 2 of 3 draws |

**The one-line answer:** on the data available today, nothing V2 built beats "today's price, with the spread of past
changes as its range" reliably. This is an expected and useful outcome (rule 11). It tells us what not to ship, and
the harness is ready to ask the same questions of real prices the day they mature.

## What V2 leaves in the product

- **Honest labels everywhere:** `data_provenance` on every forecast, metric, chart, graph edge and admin number;
  SYNTHETIC / REAL — LIMITED HISTORY / REAL badges; ESTIMATE badges on trade-flow edges.
- **Admin page:** real-data readiness per mandi and data type, evaluation runs, and a "V2 results" card linking each
  study.
- **Mandi graph:** weekly build, `GET /graph/mandi/{id}/neighbours`, and a "Connected mandis" card for policy and
  admin; optional Neo4j mirror.
- **Satellite pipeline:** a resumable Sentinel-2 fetch, 2,610 real observations for the pilot districts, and a
  validation against published statistics.
- **Unchanged for users:** the display model is still V1 LightGBM on prices + weather. TFT writes forecasts only
  behind a flag; the GNN has no writer.

## Real-data problems found and fixed on the way

- data.gov.in and satellite hosts were unreachable from the build workspace, so fetches ran on the user's PC.
- The ICRISAT apportioned database ends in 2011 and has no tomato; tomato ground truth was read from three editions
  of Horticultural Statistics at a Glance (one of which mislabels its units).
- Earth Search: items without band assets, duplicate reprocessed scenes, overlapping tiles, and an offset listed on
  items whose pixels already have it applied (verified on raw values; it had produced NDVI up to 1.93).

## When real prices mature (about 13 months after 2026-09-25)

Rule 10 asks for every comparison on real data too. None of these needs code changes:

```bash
python -m agripulse_ml.ablation --provenance real   # V1 LightGBM, naive, seasonal naive + each feature group
python -m agripulse_ml.tft.experiment        # with [tft] data_provenance = "real" in config/models.toml
python -m agripulse_ml.graph.experiment      # with [gnn] data_provenance = "real"
```

Open questions for then are tracked in docs/backlog.md (items 12–15): interval calibration, whether weather helps,
a second calendar harmonic, and a tomato-specific satellite mask.

## Documents

[backtest-synthetic.md](backtest-synthetic.md) · [feature-store.md](feature-store.md) ·
[tft-results.md](tft-results.md) · [graph-results.md](graph-results.md) ·
[satellite-results.md](satellite-results.md) · [ablation-results.md](ablation-results.md) ·
[data-sources.md](data-sources.md) · [backlog.md](backlog.md)
