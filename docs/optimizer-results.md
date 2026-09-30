# Optimizer vs the V1 rule (V3-0)

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Prices come from V2-0's 8 synthetic datasets. The lots and
> trucks are simulated. Distances are approximate: straight-line distance × 1.3 at 40 km/h, because OSRM is not
> running in the study environment. `data_provenance = "synthetic"` for every row of
> `docs/results/optimizer-synthetic.csv`.

## Real data (rule 23)

| Module | Real data today | Status |
|---|---|---|
| Decisions (which mandi, which truck) | 0 real lots with a known sale outcome, 0 real multi-lot batches | **Not yet evaluable on real data.** Scoring a decision needs the price at the mandi it was sent to *and* at the mandis it wasn't sent to, on the sale day. That needs real Agmarknet history (started 2026-09-25) and real lots from the field pilot. |
| Forecasts the optimizer uses | Real prices for about 5 days | Display-model forecasts are `real_partial` at best; calibration is `not_yet_applicable` (B-1). |
| Mandi room (typical arrivals) | Real arrivals are starting to accumulate | Uses real arrivals when there are ≥ 5 days in the last 28, otherwise synthetic (labelled). |

## Summary

- **The pre-registered switch rule is met, so the optimizer is now the default** (`[recommender] default =
  "optimizer"`). The rule was written before the study ran: at least the rule's mean realised net value, and fewer
  constraint violations.
  - **Money: effectively a tie.** Mean realised net value per decision day is ₹16,18,201 for the optimizer vs
    ₹16,17,042 for the rule, a difference of **+0.07%**.
  - **Violations: 0 vs 140.** The rule sent more tonnes to a mandi than it can absorb in 94% of the dense batches
    and 59% of the medium ones.
  - So the case for switching is the constraint violations, not the money.
- **The dependable gain is truck assignment, not mandi choice.** Keeping V1's mandis but choosing trucks
  optimally beats the rule on 80/80 medium days and 75/80 dense days (+2.0% to +2.1% net value). It cuts truck
  kilometres by 6–17%.
  - The full optimizer cuts transport cost by 22–34% and spoilage loss by 19–40%.
  - In dense batches, part of that saving is given back because it refuses to overload mandis.
- **Where it helps and where it doesn't:**
  - **Sparse batches (few lots, spare trucks):** +1.7% on average, but the per-dataset range is −2.3% to +7.5%.
    With little competition for trucks or mandis, V1's rule plus a sensible dispatcher is nearly as good.
  - **Medium batches:** the clearest win. +2.3% mean net value, better on 64/80 days, with 26% less transport cost
    and zero violations.
  - **Dense, capacity-tight batches:** **the optimizer earns less than the rule (−1.3% mean; lower in 5 of 8
    datasets).** It keeps each mandi within its room, while the rule floods the best-priced mandis (46 t over, on
    average). **The scoring does not charge for flooding**: modelling that price drop would have favoured the
    optimizer by construction. If flooding a mandi really depresses its price, which is likely but not modelled
    here, the rule's dense-batch advantage is overstated.
- **Risk-averse variant (plans on p10):** slightly lower realised value than the p50 optimizer (−0.4% to −1.1%),
  with the lowest transport cost. It is available as `objective = "p10"` and is not the default.
- **Solve time:** every sparse and medium solve is proven OPTIMAL. The median is 0.02 s (sparse) and 0.35 s
  (medium); the slowest medium solve took 5.8 s.
  - Dense batches (60 lots, about 25 trucks, 18 mandis) often hit the 10-second limit: 43 of 80 (p50) and 37 of 80
    (p10) returned FEASIBLE rather than proven OPTIMAL. Their numbers are what a 10-second budget buys, not the
    true optimum.
- **None of this comes from better forecasting.** Every method used the same calibrated V1 forecast, which V2
  found is no better than naive. The optimizer's value is in respecting capacity and mandi room and in choosing
  the right trucks.

## Setup

| | |
|---|---|
| Datasets | V2-0's 8 synthetic datasets (seeds 1–8), 18 mandis. |
| Forecast | V1 LightGBM (the display model), B-1 track-record calibration, 1-week horizon (what `/recommend/best-mandi` uses). V2-0's final 8 folds; the walk-forward is extended back 13 folds only to build the calibration track record. |
| Decision days | 10 per dataset, evenly spaced over the scored folds: 80 days. |
| Batches | Seeded lots around 8 Karnataka tomato belts (median 3 t, 0.5–9 t); trucks of 2.5/5/9/10 t in 5 fleet towns. Sparse: 6 lots, 8 trucks. Medium: 20 lots, 18 trucks. Dense: 60 lots, trucks for about 70% of the tonnage. |
| Scoring | Every plan is scored at the price **realised one week later** at the mandi it chose, not the forecast it planned on. One cost model is used for every method. |
| Cost model (`config/recommender.toml`, assumptions) | Truck: ₹18/km + ₹4/km per tonne of capacity, return leg included. Spoilage: V1 formula (0.4%/h at 30 °C, Q10 = 2). |
| Hard limits (optimizer) | Truck capacity; spoilage ≤ 8% per lot; tonnes into a mandi ≤ 25% of its typical daily arrivals (median of the last 28 days). |

**Methods**
- **V1 rule (old):** each lot goes to its own top-ranked mandi, exactly as `/recommend/best-mandi` ranks it. The
  largest lot is dispatched first, to the nearest free truck that fits.
  - V1 never assigned trucks; the dispatcher is added so the comparison is fair.
  - It was chosen before the run. A first draft used smallest-truck-first, which ignores distance and made the
    optimizer look better than it is.
- **V1 mandis + optimal trucks:** V1's mandi choices, with only the truck assignment solved by CP-SAT. This
  isolates how much of the gain is truck assignment.
- **Optimizer:** CP-SAT over all lots, mandis and trucks together, maximising p50 net value under the hard limits.
- **Optimizer, risk-averse:** the same, planning on p10.

## Results

All money is in lakh (L) rupees per decision day. "Violations" counts lots over the spoilage limit plus mandis
filled beyond their room.

**Sparse: 6 lots, 8 trucks** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | max solve |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹4.52 L | – | – | ₹1.19 L | ₹0.10 L | 2,772 | 5.8 | 1 | 0.0 | 0.0 s |
| V1 mandis + optimal trucks | ₹4.60 L | +1.8% | 68 / 80 | ₹1.07 L | ₹0.10 L | 2,609 | 5.7 | 1 | 0.0 | 0.0 s |
| **Optimizer** | ₹4.59 L | +1.7% | 61 / 80 | ₹0.93 L | ₹0.08 L | 2,253 | 5.7 | 0 | 0.0 | 0.0 s |
| Optimizer, risk-averse (p10) | ₹4.57 L | +1.1% | 56 / 80 | ₹0.89 L | ₹0.08 L | 2,154 | 5.6 | 0 | 0.0 | 0.0 s |

Optimizer vs V1 rule, mean realised net value per dataset: 1: +1.1%, 2: +2.7%, 3: +5.6%, 4: +7.5%, 5: -1.0%, 6: -2.3%, 7: +4.6%, 8: -1.5%

**Medium: 20 lots, 18 trucks** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | max solve |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹14.72 L | – | – | ₹3.41 L | ₹0.33 L | 8,495 | 17.3 | 49 | 9.6 | 0.0 s |
| V1 mandis + optimal trucks | ₹15.01 L | +2.0% | 80 / 80 | ₹3.05 L | ₹0.32 L | 7,680 | 17.1 | 45 | 9.5 | 0.0 s |
| **Optimizer** | ₹15.06 L | +2.3% | 64 / 80 | ₹2.52 L | ₹0.24 L | 6,306 | 17.2 | 0 | 0.0 | 2.0 s |
| Optimizer, risk-averse (p10) | ₹15.00 L | +1.9% | 62 / 80 | ₹2.37 L | ₹0.23 L | 5,928 | 17.0 | 0 | 0.0 | 5.8 s |

Optimizer vs V1 rule, mean realised net value per dataset: 1: +2.3%, 2: +3.8%, 3: +4.3%, 4: +5.6%, 5: +1.0%, 6: -1.1%, 7: +1.4%, 8: +1.9%

**Dense: 60 lots, trucks for ~70% of the tonnes** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | max solve |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹29.28 L | – | – | ₹5.23 L | ₹0.60 L | 13,039 | 27.3 | 90 | 46.5 | 0.0 s |
| V1 mandis + optimal trucks | ₹29.88 L | +2.1% | 75 / 80 | ₹4.39 L | ₹0.56 L | 10,880 | 27.3 | 89 | 48.0 | 0.1 s |
| **Optimizer** | ₹28.89 L | -1.3% | 48 / 80 | ₹3.44 L | ₹0.36 L | 8,444 | 27.3 | 0 | 0.0 | 10.6 s |
| Optimizer, risk-averse (p10) | ₹28.56 L | -2.4% | 50 / 80 | ₹3.30 L | ₹0.35 L | 8,103 | 27.3 | 0 | 0.0 | 10.5 s |

Optimizer vs V1 rule, mean realised net value per dataset: 1: -1.3%, 2: -3.3%, 3: +1.4%, 4: +2.1%, 5: -2.7%, 6: -0.3%, 7: +0.2%, 8: -4.3%

**Planned vs realised.** Every method realised 4–5% less than it planned at the forecast p50. The same optimism
applies to all methods, so it doesn't change the comparison. It matches B-1's finding that the p50 is slightly
above the outcome (median bias −0.1% to −2.4%).

**The spoilage limit never bound.** 8% expected loss is about 20 hours at 30 °C, and no Karnataka trip here comes
close, so that limit made no difference. A tighter limit would matter only with evidence that tomato quality falls
faster. It is left unchanged, because tuning it after seeing results would break the pre-registration.

## Honest reading

1. The optimizer is a better **dispatcher**: same tonnes shipped, 22–34% less transport cost. It is not a better
   price picker, because every method used the same forecast, and V2 showed that forecast is not better than
   naive.
2. The mandi-room limit is the optimizer's main cost in dense batches. Whether it is worth that depends on how
   much a flooded mandi's price actually falls. That is **not measured anywhere in this project yet**. It is the
   most important thing to learn from real arrivals and prices (backlog 23).
3. On money alone the two methods tie. The switch is justified by zero violations at no average cost, not by a
   money gain.

## Product

- `/recommend/best-mandi` (farmer) now goes through the optimizer. It uses the same ranking data, the truck cost
  model (the smallest standard truck that fits, hired at the farm, paid both ways), and the two hard limits.
- Mandi room subtracts the tonnes **already on the road** to that mandi, from V1 tracking. Options that break a
  limit are listed last with the reason. V1's formula is still used when `default = "rule"`.
- **Admin → "Recommenders: V1 rule vs optimizer":** runs both on a simulated batch against today's real mandis and
  forecasts, and shows net value, transport, spoilage, lots shipped, violations and solve time. That view is scored
  at the forecast (a planning view); only this study scores at realised prices.

## Reproduce

```
python -m agripulse_ml.decision_study      # ~40 min: docs/results/optimizer-synthetic.csv, optimizer-meta.json
pytest tests/test_decisions.py             # includes: config default == the pre-registered verdict on the CSV
```

## Known issues

- Mandi price impact of oversupply is not modelled; it is counted as a violation only (backlog 23).
- Dense batches often hit the 10 s limit (43/80 FEASIBLE, not proven OPTIMAL). A longer limit or a warm start from
  the rule's plan may help (backlog 24).
- One lot per truck; consolidation is V3-1.
- Distances are approximate in the study; production uses OSRM when `OSRM_URL` is set.
- Truck rates, the spoilage limit and the mandi room share are assumptions; replace them with fleet quotes and
  field data.
