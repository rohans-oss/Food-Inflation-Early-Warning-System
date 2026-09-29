# V2-3 results: mandi graph + GNN vs non-graph models

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Every price number here comes from the synthetic generator.
> Rows marked **PLANTED SIGNAL** come from a generator variant built to contain a spatial effect (a positive control).
> They test the method, never tomato prices. Graph distance edges are real (straight-line × 1.3 until OSRM runs);
> correlation edges are synthetic; flow edges are **ESTIMATES**.

## Bottom line

1. **Graph features help LightGBM a little, consistently, but not enough to beat naive.** Adding the `graph` feature
   group improves LightGBM's pinball loss by 1–3% on the pinned generator. The same happens on all 3 draws at every
   horizon (12 of 12 draw-horizon pairs). That lifts LightGBM from "slightly worse than naive" (−1.7% to −3.1%) to
   "about the same as naive" (−1.1% to +0.6%).
2. **This is not evidence that geography matters.** In the pinned generator, distance carries *no* information by
   construction. The gain comes from neighbours' price changes averaging out each mandi's own noise and revealing the
   shared regional movement. On real data that effect may or may not exist.
3. **The GNN loses clearly.** It is 25–32% worse than naive on the pinned generator and 10–16% worse on the
   planted one. It is unstable across draws: on draw 1 it is 64–102% worse than naive.
4. **The positive control is only a weak pass.** With a planted spatial signal, message passing starts to help
   (GNN vs the same network without edges goes from −3% to −12% to +3% to +8%), and LightGBM's graph gain grows
   slightly at 1 week (+3.0% → +4.4%). But far mandis, which should gain most from a signal that reaches them up
   to 16 days late, gain *no more* than near ones. So neither model is clearly exploiting the planted lead-lag
   structure. The likely reason is that the signal only exists during spike onsets, a small share of the 4 × 28
   test days per draw. The control is under-powered rather than a proof either way.

As approved, nothing was tuned after seeing these numbers (rule 11). `config/models.toml` `[gnn]` was fixed before the
run.

**Reproducibility check:** on the pinned generator, `lightgbm_v1` matches V2-2 (docs/tft-results.md) to the decimal
(−2.5 / −3.1 / −2.7 / −1.7% vs naive), so the two phases share the same folds and rows.

## Setup

| | |
|---|---|
| Data | Synthetic generator, 18 mandis, 2022-01-01 → 2026-09-25, seeds 7, 1, 2. Two generators: `random` (pinned baseline: each mandi feels a spike 0–3 days late at random) and `distance` (**PLANTED SIGNAL**: spikes start at Kolar and reach each mandi after road-km ÷ 40 km/day, 0–16 days) |
| Feature table | `prices+weather+graph`, publication lags applied |
| Folds | Shared harness, 4 × 28-day walk-forward folds (cutoffs 2026-05-09 … 08-01), the same for every model |
| Graph | Distance edges: 5 nearest within 300 road-km, weight exp(−km/100). Correlation edges: 7-day log-price changes over the last 365 days, ≥ 0.3, top 5, symmetrised. Flow **ESTIMATE**: arrivals × (price gap − 1.2 Rs/quintal/km) ÷ km, top 3 out-edges, scaled 0–1. Rebuilt every 28 days, each snapshot from data published by its date |
| Models | `seasonal_naive`, `naive`, `lightgbm_v1` (V1 LightGBM, non-graph columns), `lightgbm_graph` (+ the 7 `gr_*` columns), `gru_nograph` (GRU + 2 dense layers, no edges: the control), `gnn` (same network + 2 graph-convolution layers over distance + correlation + flow edges, fold-cutoff snapshot) |
| GNN | Plain torch: 28-day window, hidden 32, Adam 1e-3, up to 40 epochs, early stopping on 60 days, split-conformal calibration on 120 days (same as TFT / LightGBM), trained spike head (not an approximation) |
| Raw results | `docs/results/graph-synthetic.csv` (long table with `generator` and `seed`), `docs/results/graph-synthetic-timings.json` |

## Pinned generator (`random`: distance carries no information)

Pinball loss, pooled over mandis, mean of 3 draws (Rs/quintal, lower is better):

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| seasonal_naive | 187.5 | 269.2 | 338.7 | 389.8 |
| naive | 131.1 | **189.5** | **237.2** | **275.0** |
| lightgbm_v1 | 133.2 | 193.8 | 244.2 | 286.3 |
| lightgbm_graph | **129.3** | 189.8 | 240.7 | 284.2 |
| gru_nograph | 155.5 | 218.1 | 281.0 | 330.6 |
| gnn | 164.6 | 248.2 | 315.9 | 349.9 |

% better than naive: mean (draws won out of 3) [worst, best draw]:

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| lightgbm_v1 | -2.5 (2/3) [-10, +2] | -3.1 (1/3) [-10, +3] | -2.7 (1/3) [-5, +1] | -1.7 (1/3) [-10, +5] |
| lightgbm_graph | +0.6 (2/3) [-6, +6] | -0.8 (1/3) [-6, +5] | -1.1 (1/3) [-5, +3] | -0.7 (1/3) [-9, +7] |
| gru_nograph | -19.5 (0/3) [-32, -2] | -15.3 (0/3) [-30, -6] | -17.9 (0/3) [-43, -3] | -18.5 (1/3) [-48, +4] |
| gnn | -24.7 (1/3) [-64, +2] | -31.2 (1/3) [-89, +3] | -32.4 (1/3) [-102, +6] | -25.4 (1/3) [-78, +12] |

**Paired graph effect** (% better than the same model without the graph):

| comparison | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| lightgbm_graph vs lightgbm_v1 | +3.0 (3/3) | +2.2 (3/3) | +1.6 (3/3) | +1.1 (3/3) |
| gnn vs gru_nograph | -3.4 (2/3) | -11.6 (2/3) | -8.8 (2/3) | -3.6 (2/3) |

## Positive control (`distance`: PLANTED SIGNAL, not a result about prices)

| model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| seasonal_naive | 187.0 | 267.4 | 333.1 | 378.4 |
| naive | 129.1 | **179.7** | **225.0** | **261.9** |
| lightgbm_v1 | 134.6 | 185.4 | 231.8 | 272.6 |
| lightgbm_graph | **128.7** | 179.8 | 227.8 | 269.9 |
| gru_nograph | 159.3 | 220.6 | 270.5 | 321.5 |
| gnn | 145.4 | 207.7 | 249.5 | 312.8 |

| comparison | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|
| lightgbm_graph vs lightgbm_v1 | +4.4 (3/3) | +3.1 (3/3) | +1.5 (3/3) | +1.1 (3/3) |
| gnn vs gru_nograph | +8.3 (3/3) | +2.9 (1/3) | +6.3 (1/3) | +3.8 (2/3) |
| lightgbm_graph vs naive | -0.4 (2/3) | -1.1 (2/3) | -1.6 (1/3) | -1.5 (2/3) |
| gnn vs naive | -13.0 (0/3) | -15.7 (0/3) | -9.7 (0/3) | -14.1 (1/3) |

**Does the graph help the mandis the planted signal reaches late?** The per-mandi graph effect (averaged over draws
and horizons), split by planted delay:

| | near Kolar (delay ≤ 3 days) | far (delay ≥ 8 days) | correlation of effect with delay |
|---|---|---|---|
| lightgbm_graph vs lightgbm_v1, planted | +2.8% | +2.5% | −0.06 |
| gnn vs gru_nograph, planted | +6.4% | +4.8% | −0.25 |
| lightgbm_graph vs lightgbm_v1, pinned | +2.4% | +1.6% | −0.24 |

If the models were reading the planted lead-lag, the far column would be clearly larger. It isn't.

## Intervals and spikes

p10–p90 coverage (target 80%). Graph models are no better calibrated than the rest:

| generator | model | 1 wk | 2 wk | 3 wk | 4 wk |
|---|---|---|---|---|---|
| random | naive | 84.1 | 83.7 | 83.6 | 82.3 |
| random | lightgbm_v1 | 72.9 | 67.0 | 61.9 | 59.5 |
| random | lightgbm_graph | 74.2 | 68.1 | 62.2 | 60.3 |
| random | gru_nograph | 66.8 | 59.6 | 52.3 | 49.1 |
| random | gnn | 61.9 | 55.2 | 48.8 | 45.4 |
| distance | lightgbm_graph | 74.9 | 69.4 | 63.8 | 60.9 |
| distance | gnn | 67.7 | 59.4 | 57.6 | 54.8 |

Spike warning (> 30% rise within 14 days, alert at probability 0.5, mean of 3 draws; about 470 spike days per draw):

| generator | model | recall | precision | false-alarm rate | Brier (base rate ≈ 0.18) |
|---|---|---|---|---|---|
| random | lightgbm_v1 | 0.099 | 0.330 | 0.069 | 0.183 |
| random | lightgbm_graph | 0.125 | 0.372 | 0.068 | **0.179** |
| random | gru_nograph | 0.295 | 0.343 | 0.189 | 0.210 |
| random | gnn | 0.242 | 0.261 | 0.174 | 0.225 |
| distance | lightgbm_v1 | 0.150 | 0.376 | 0.093 | 0.183 |
| distance | lightgbm_graph | 0.151 | 0.412 | 0.068 | **0.175** |
| distance | gru_nograph | 0.178 | 0.380 | 0.146 | 0.216 |
| distance | gnn | 0.163 | 0.312 | 0.120 | 0.189 |

`lightgbm_graph` is the only V2 model so far whose spike probability scores slightly better than the base rate
(Brier 0.175–0.179 vs 0.180–0.182). The margin is small and synthetic. The neural models catch more spikes but raise
2–3× the false alarms, and their probabilities are worse than the base rate.

## Investigation (no tuning)

- **GNN instability.** Draw 1 on the pinned generator is where both neural models collapse (gru_nograph −32% to
  −48%, GNN −64% to −102%), and it is the draw where they trained longest (15–18 epochs vs 7–9 elsewhere). The
  early-stopping window chose later, more over-fitted weights there. Same caveat as TFT: tuning would address it,
  and tuning is out of scope for this phase.
- **Why message passing hurts on the pinned generator.** With no spatial structure, the graph layers mostly mix in
  neighbours' noise. On the planted generator the sign flips, which is the expected direction for a working method.
- **Why the control is weak.** The planted signal only exists while a spike travels, a few events in 5 years. Each
  draw's 4 test folds hold few spike onsets, so a lead-lag effect has little room to show in pooled pinball loss.
  A stronger control (more frequent planted shocks) was not run, so as not to design the test around a desired result.
- **Graph edges.** The last snapshot has 100 distance, 104–114 correlation and 38 flow-ESTIMATE edges. Correlation edges appear
  from the first snapshot with 60+ days of overlap. The first snapshot has only distance edges plus one-day flow
  estimates.

## Compute

Six draws (2 generators × 3 seeds × 4 folds, 6 models) took 21.9 minutes on 2 CPU cores. A GNN fit takes 10–55 s
(7–18 epochs). LightGBM takes seconds.

| generator | draw | wall (min) | GNN epochs | GNN fit (s) | no-graph epochs | no-graph fit (s) |
|---|---|---|---|---|---|---|
| random | 7 | 2.4 | 8.5 | 12 | 6.5 | 10 |
| random | 1 | 7.4 | 17.5 | 55 | 15.5 | 41 |
| random | 2 | 3.5 | 14.2 | 21 | 14.0 | 20 |
| distance | 7 | 2.1 | 7.0 | 10 | 6.5 | 10 |
| distance | 1 | 3.5 | 9.5 | 15 | 17.2 | 26 |
| distance | 2 | 2.9 | 12.2 | 19 | 8.8 | 13 |

(The random/1 draw overlapped with a web build and a browser check, and its fits also ran the most epochs.)

## Reproduce

```bash
pip install -e .[graph]
python -m agripulse_ml.graph.experiment --record          # ~22 min on 2 cores; writes docs/results/graph-synthetic.*
python -m agripulse_ml.graph.experiment --generators random --seeds 7 --folds 1   # ~2 min
```

## What is in the product

- `graph` feature group in the feature store (leakage-tested). LightGBM stays on `prices+weather` in production:
  switching the display model is a decision for real data, not for this synthetic result.
- `graph_edges` table + `python -m agripulse_ml.graph.build` (weekly job), `GET /graph/mandi/{id}/neighbours`,
  and a "Connected mandis" card on the policy and admin pages. Every row shows its provenance, and flow rows carry an
  ESTIMATE badge.
- Optional Neo4j mirror (`docker compose --profile graph up -d neo4j`, `[graph] backend = "neo4j"`).
- The GNN has no forecast writer. Given these results there is nothing to show users; adding one is the TFT
  pattern (`tft/forecast.py`) if real data ever makes it worth it.
