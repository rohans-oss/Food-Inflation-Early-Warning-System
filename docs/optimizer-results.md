# Decisions: optimizer vs the V1 rule (V3-0) and shared / return loads (V3-1)

> **SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT.** Prices come from V2-0's 8 synthetic datasets. The lots and
> trucks are simulated. Distances are approximate: straight-line distance × 1.3 at 40 km/h, because OSRM is not
> running in the study environment. Every row of `docs/results/optimizer-synthetic.csv` and
> `consolidation-synthetic.csv` has `data_provenance = "synthetic"`.

## Real data (rule 23)

| Module | Real data today | Status |
|---|---|---|
| Decisions (which mandi, which truck, shared loads, return loads) | 0 real lots with a known sale outcome; 0 real multi-lot batches; 0 real return trips | **Not yet evaluable on real data.** Scoring a decision needs the realised price at the mandi chosen *and* at the ones not chosen. That needs real Agmarknet history (started 2026-09-25) and real lots and trips from the field pilot. |
| Forecasts the optimizer uses | Real prices for about 5 days | Display-model forecasts are `real_partial` at best; calibration is `not_yet_applicable` (B-1). |
| Mandi room (typical arrivals) | Real arrivals are starting to accumulate | Uses real arrivals when there are ≥ 5 days in the last 28, otherwise synthetic (labelled). |
| Distances and trips for return loads | Real (OSRM when configured; the V1 trips table) | The mechanics run on real data; their value is unproven until the field pilot. |

## Summary

**V3-0: the optimizer is the default recommender.**
- The pre-registered switch rule was met. It required at least the rule's mean realised net value and fewer
  violations.
- **Money: effectively a tie.** Mean realised net value per decision day is ₹16,20,608 for the optimizer vs
  ₹16,17,042 for the rule (+0.2%).
- **Violations: 0 vs 140.** The rule overfilled a mandi on 94% of dense days and 59% of medium days.
- **The dependable gain is truck assignment.** V1's mandis with optimal trucks beat the rule on 80/80 medium and
  75/80 dense days. The full optimizer cuts transport cost by 22–34%.
- **In dense batches the optimizer earns 1.1% less than the rule** (lower in 5 of 8 datasets). It refuses to
  overfill mandis, and the scoring does not charge for overfilling (see honest reading).

**V3-1: shared truckloads and return loads are switched on.**
- The pre-registered rule was met. It required at least V3-0's mean realised net value and no more violations.
- **Shared + return loads beat V3-0 on 204 of 240 days, tie on 34, and are worse on 2.** Violations stay at 0.
- By batch, versus V3-0:
  - **Sparse:** +2.4% (lots spread out) and +5.0% (clustered).
  - **Medium:** +7.9% and +13.0%.
  - **Dense:** +47% and +38%.
- **Most of the dense gain is shipping more lots with the same trucks,** 53 of 60 instead of 27. **A lot left
  unshipped counts as ₹0 in this scoring,** which overstates the gain: in reality it would be sold locally or the
  next day.
- **Per tonne actually shipped, the gain is modest and robust.** Shared loads cut transport cost per tonne by
  11–18% and raise net value per tonne by 1–3%.
- **Sharing helps most when lots are clustered and trucks are scarce.** With few, spread-out lots it barely matters:
  only 14 of 40 sparse, spread days improve.
- **Return loads mostly matter when trucks are short.** On their own they add about 2 return trips per medium day and
  14–20 per dense day, but almost none in sparse batches.

**Neither version forecasts better.** Every method uses the same calibrated V1 forecast, which V2 found is no better
than naive. All of these gains come from constraint handling, truck assignment and routing.

## Reproducibility (fixed during V3-1)

The first V3-0 run stopped CP-SAT after 10 wall-clock seconds with 8 workers on a 2-core machine. Dense results
therefore depended on how busy the machine was: re-solving one dense batch while another job was running gave a plan
worth ₹5.6 L instead of ₹10.4 L.

The solver now stops on a **deterministic work limit** (`deterministic_time_limit = 10`) with 1 worker, so a batch
gets the same plan on any machine at any load. The API keeps a 30-second wall-clock cap as a safety net. V3-0 was
re-run with this setting:
- Sparse and medium results are identical.
- Dense changed slightly: −1.3% → −1.1% vs the rule.
- The verdict is unchanged.

The tables below are from the re-run.

## Setup (both studies)

| | |
|---|---|
| Datasets | V2-0's 8 synthetic datasets (seeds 1–8), 18 mandis. |
| Forecast | V1 LightGBM (the display model), B-1 track-record calibration, 1-week horizon (what `/recommend/best-mandi` uses). V2-0's final 8 folds; the walk-forward is extended back 13 folds only to build the calibration track record. Cached in `data/cache/`. |
| Decision days | V3-0: 10 per dataset (80 in all). V3-1: every second V3-0 day (40 in all, to fit the compute budget). |
| Batches | Seeded lots around 8 Karnataka tomato belts (median 3 t, 0.5–9 t); trucks of 2.5/5/9/10 t in 5 fleet towns. Sparse: 6 lots, 8 trucks. Medium: 20 lots, 18 trucks. Dense: 60 lots, trucks for about 70% of the tonnage. V3-1 also has each batch **spread** over 8 belts or **clustered** in 2. |
| Scoring | Every plan is scored at the price **realised one week later** at the mandi it chose, with one cost model for every method (`agripulse_api.decisions.evaluate`). A truck's cost is its whole day's route. Each lot's spoilage runs from its own pickup to its mandi. |
| Cost model (`config/recommender.toml`, assumptions) | Truck: ₹18/km + ₹4/km per tonne of capacity, drive home included. Spoilage: V1 formula (0.4%/h at 30 °C, Q10 = 2), plus 20 minutes per extra pickup stop. |
| Hard limits | Truck capacity; spoilage ≤ 8% per lot; tonnes into a mandi ≤ 25% of its typical daily arrivals, minus trucks already on the way in live use. V3-1 adds a 12-hour driver day, applied to shared loads and return trips. |

## V3-0: the V1 rule vs the optimizer

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

All money is in lakh (L) rupees per decision day. "Violations" counts lots over the spoilage limit plus mandis
filled beyond their room.

**Sparse: 6 lots, 8 trucks** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | stopped at work limit |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹4.52 L | – | – | ₹1.19 L | ₹0.10 L | 2,772 | 5.8 | 1 | 0.0 | – |
| V1 mandis + optimal trucks | ₹4.60 L | +1.8% | 68 / 80 | ₹1.07 L | ₹0.10 L | 2,609 | 5.7 | 1 | 0.0 | 0 / 80 |
| **Optimizer** | ₹4.59 L | +1.7% | 61 / 80 | ₹0.93 L | ₹0.08 L | 2,253 | 5.7 | 0 | 0.0 | 0 / 80 |
| Optimizer, risk-averse (p10) | ₹4.57 L | +1.1% | 56 / 80 | ₹0.89 L | ₹0.08 L | 2,154 | 5.6 | 0 | 0.0 | 0 / 80 |

Optimizer vs V1 rule, mean realised net value per dataset: 1: +1.1%, 2: +2.7%, 3: +5.6%, 4: +7.5%, 5: -1.0%, 6: -2.3%, 7: +4.6%, 8: -1.5%

**Medium: 20 lots, 18 trucks** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | stopped at work limit |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹14.72 L | – | – | ₹3.41 L | ₹0.33 L | 8,495 | 17.3 | 49 | 9.6 | – |
| V1 mandis + optimal trucks | ₹15.01 L | +2.0% | 80 / 80 | ₹3.05 L | ₹0.32 L | 7,680 | 17.1 | 45 | 9.5 | 0 / 80 |
| **Optimizer** | ₹15.06 L | +2.3% | 64 / 80 | ₹2.52 L | ₹0.24 L | 6,306 | 17.2 | 0 | 0.0 | 0 / 80 |
| Optimizer, risk-averse (p10) | ₹15.00 L | +1.9% | 62 / 80 | ₹2.37 L | ₹0.23 L | 5,928 | 17.0 | 0 | 0.0 | 0 / 80 |

Optimizer vs V1 rule, mean realised net value per dataset: 1: +2.3%, 2: +3.8%, 3: +4.3%, 4: +5.6%, 5: +1.0%, 6: -1.1%, 7: +1.4%, 8: +1.9%

**Dense: 60 lots, trucks for ~70% of the tonnes** (80 decision days: 8 datasets x 10 days)

| method | realised net value (mean) | vs V1 rule | days it beats the rule | transport | spoilage loss | truck km | lots shipped | violations (total) | t over mandi room (mean) | stopped at work limit |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 rule (old) | ₹29.28 L | – | – | ₹5.23 L | ₹0.60 L | 13,039 | 27.3 | 90 | 46.5 | – |
| V1 mandis + optimal trucks | ₹29.88 L | +2.1% | 75 / 80 | ₹4.39 L | ₹0.56 L | 10,880 | 27.3 | 89 | 48.0 | 0 / 80 |
| **Optimizer** | ₹28.96 L | -1.1% | 49 / 80 | ₹3.44 L | ₹0.36 L | 8,420 | 27.3 | 0 | 0.0 | 19 / 80 |
| Optimizer, risk-averse (p10) | ₹28.81 L | -1.6% | 50 / 80 | ₹3.23 L | ₹0.34 L | 7,907 | 27.3 | 0 | 0.0 | 10 / 80 |

Optimizer vs V1 rule, mean realised net value per dataset: 1: -0.8%, 2: -3.1%, 3: +1.4%, 4: +2.4%, 5: -2.7%, 6: -0.3%, 7: +0.5%, 8: -3.7%

**Readings**
- The optimizer is a better **dispatcher**: the same tonnes shipped for 22–34% less transport cost.
- The risk-averse variant earns 0.4–0.6% less than the p50 optimizer, with the lowest transport cost.
- The spoilage limit never bound, because no Karnataka trip here comes close to 8%.
- Every method realised 3–4% less than it planned, which fits B-1's finding that the p50 sits slightly above the
  outcome.
- Solve times: sparse and medium are always proven optimal. The slowest medium solve took 4 s. Dense solves take a
  median of 7.5 s; 19 of 80 (p50) stopped at the work limit.

## V3-1: shared truckloads and return loads

**Methods** (identical batches; V3-1 adds the spread / clustered layouts):
- **V1 rule** and **V3-0 optimizer**: as above.
- **Shared loads** (`decisions/loads.py`):
  - Candidates are each lot alone, plus groups of up to 4 lots drawn from each lot's 6 nearest pickups within
    20 km that fit on some truck.
  - The pickup order is exact: all orders are checked (at most 4! = 24).
  - CP-SAT picks (load, truck, mandi). Lots travelling alone keep all of V3-0's options, so any V3-0 plan remains
    possible and sharing is chosen only when it plans better.
- **Return loads** (`decisions/returns.py`):
  - After its first delivery, each truck may take one lot that is still waiting: first mandi → that lot → a second
    mandi → home.
  - It must pay for the extra kilometres, fit the 12-hour day and the spoilage cap, and fit the second mandi's
    remaining room.
- **Shared + return loads:** both together, which is what users get.

**Pre-registered switch:** shared + return loads must earn ≥ the V3-0 optimizer on mean realised net value, with no
more violations. Result: ₹19.48 L vs ₹15.05 L mean net value, and 0 vs 0 violations. **Met, so both are switched
on.**

"vs V3-0" and "days better" compare with the V3-0 optimizer on the same batch.

**Sparse: 6 lots, 8 trucks**

| layout | method | realised net value | vs V3-0 | days better than V3-0 | lots shipped | trucks | shared loads | return trips | transport | empty km | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | V1 rule (old) | ₹4.02 L | – | – | 5.7 / 6 | 5.7 | 0.0 | 0.0 | ₹1.10 L | 1,813 | 0 |
|  | V3-0 optimizer (1 lot/truck) | ₹4.11 L | – | – | 5.7 / 6 | 5.7 | 0.0 | 0.0 | ₹0.86 L | 1,437 | 0 |
|  | V3-0 + return loads | ₹4.13 L | +0.6% | 3 / 40 | 5.7 / 6 | 5.7 | 0.0 | 0.1 | ₹0.86 L | 1,440 | 0 |
|  | Shared loads | ₹4.20 L | +2.1% | 14 / 40 | 5.7 / 6 | 5.2 | 0.4 | 0.0 | ₹0.82 L | 1,364 | 0 |
|  | **Shared + return loads** | ₹4.21 L | +2.4% | 16 / 40 | 5.8 / 6 | 5.2 | 0.4 | 0.1 | ₹0.82 L | 1,366 | 0 |
| clustered | V1 rule (old) | ₹3.82 L | – | – | 5.8 / 6 | 5.8 | 0.0 | 0.0 | ₹1.24 L | 2,021 | 0 |
|  | V3-0 optimizer (1 lot/truck) | ₹3.95 L | – | – | 5.7 / 6 | 5.7 | 0.0 | 0.0 | ₹0.97 L | 1,591 | 0 |
|  | V3-0 + return loads | ₹4.02 L | +1.8% | 5 / 40 | 5.8 / 6 | 5.7 | 0.0 | 0.1 | ₹0.97 L | 1,598 | 0 |
|  | Shared loads | ₹4.08 L | +3.2% | 26 / 40 | 5.7 / 6 | 4.8 | 0.8 | 0.0 | ₹0.83 L | 1,288 | 0 |
|  | **Shared + return loads** | ₹4.15 L | +5.0% | 28 / 40 | 5.8 / 6 | 4.8 | 0.8 | 0.1 | ₹0.84 L | 1,293 | 0 |

**Medium: 20 lots, 18 trucks**

| layout | method | realised net value | vs V3-0 | days better than V3-0 | lots shipped | trucks | shared loads | return trips | transport | empty km | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | V1 rule (old) | ₹13.54 L | – | – | 17.3 / 20 | 17.3 | 0.0 | 0.0 | ₹3.27 L | 5,708 | 18 |
|  | V3-0 optimizer (1 lot/truck) | ₹13.93 L | – | – | 17.1 / 20 | 17.1 | 0.0 | 0.0 | ₹2.43 L | 4,251 | 0 |
|  | V3-0 + return loads | ₹14.62 L | +4.9% | 38 / 40 | 18.9 / 20 | 17.1 | 0.0 | 1.8 | ₹2.56 L | 4,406 | 0 |
|  | Shared loads | ₹14.67 L | +5.3% | 38 / 40 | 18.6 / 20 | 15.3 | 2.8 | 0.0 | ₹2.25 L | 3,752 | 0 |
|  | **Shared + return loads** | ₹15.03 L | +7.9% | 40 / 40 | 19.2 / 20 | 15.3 | 2.8 | 0.6 | ₹2.29 L | 3,803 | 0 |
| clustered | V1 rule (old) | ₹12.90 L | – | – | 17.6 / 20 | 17.6 | 0.0 | 0.0 | ₹3.76 L | 6,770 | 26 |
|  | V3-0 optimizer (1 lot/truck) | ₹13.26 L | – | – | 16.9 / 20 | 16.9 | 0.0 | 0.0 | ₹2.76 L | 4,903 | 0 |
|  | V3-0 + return loads | ₹14.20 L | +7.1% | 38 / 40 | 19.2 / 20 | 16.9 | 0.0 | 2.3 | ₹2.89 L | 5,069 | 0 |
|  | Shared loads | ₹14.59 L | +10.0% | 38 / 40 | 19.0 / 20 | 13.4 | 4.1 | 0.0 | ₹2.21 L | 3,579 | 0 |
|  | **Shared + return loads** | ₹14.99 L | +13.0% | 40 / 40 | 19.6 / 20 | 13.4 | 4.1 | 0.6 | ₹2.25 L | 3,620 | 0 |

**Dense: 60 lots, trucks for ~70% of the tonnes**

| layout | method | realised net value | vs V3-0 | days better than V3-0 | lots shipped | trucks | shared loads | return trips | transport | empty km | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|
| spread | V1 rule (old) | ₹28.49 L | – | – | 26.9 / 60 | 26.9 | 0.0 | 0.0 | ₹4.85 L | 8,347 | 45 |
|  | V3-0 optimizer (1 lot/truck) | ₹28.43 L | – | – | 26.9 / 60 | 26.9 | 0.0 | 0.0 | ₹3.21 L | 5,324 | 0 |
|  | V3-0 + return loads | ₹39.60 L | +39.3% | 40 / 40 | 46.5 / 60 | 26.9 | 0.0 | 19.6 | ₹4.40 L | 6,903 | 0 |
|  | Shared loads | ₹32.74 L | +15.2% | 40 / 40 | 39.8 / 60 | 26.9 | 9.8 | 0.0 | ₹3.15 L | 5,242 | 0 |
|  | **Shared + return loads** | ₹41.79 L | +47.0% | 40 / 40 | 53.1 / 60 | 26.9 | 9.8 | 13.4 | ₹3.96 L | 6,244 | 0 |
| clustered | V1 rule (old) | ₹27.08 L | – | – | 27.0 / 60 | 27.0 | 0.0 | 0.0 | ₹5.84 L | 10,549 | 48 |
|  | V3-0 optimizer (1 lot/truck) | ₹26.63 L | – | – | 26.7 / 60 | 26.7 | 0.0 | 0.0 | ₹4.48 L | 7,822 | 0 |
|  | V3-0 + return loads | ₹33.89 L | +27.3% | 40 / 40 | 40.6 / 60 | 26.7 | 0.0 | 13.8 | ₹5.24 L | 8,874 | 0 |
|  | Shared loads | ₹30.60 L | +14.9% | 40 / 40 | 40.2 / 60 | 26.6 | 10.1 | 0.0 | ₹4.53 L | 7,742 | 0 |
|  | **Shared + return loads** | ₹36.73 L | +38.0% | 40 / 40 | 49.8 / 60 | 26.6 | 10.1 | 9.6 | ₹5.08 L | 8,452 | 0 |

**Per tonne actually shipped** (so lots left at the farm don't count as ₹0):

| method | net value per tonne shipped (sparse / medium / dense) | transport per tonne (sparse / medium / dense) | tonnes shipped (sparse / medium / dense) |
|---|---|---|---|
| V1 rule | ₹20,155 / 20,852 / 22,404 | ₹6,381 / 5,676 / 4,343 | 19 / 63 / 124 |
| V3-0 optimizer | ₹20,980 / 21,668 / 22,392 | ₹4,942 / 4,213 / 3,151 | 19 / 62 / 123 |
| V3-0 + return loads | ₹20,975 / 21,617 / 21,592 | ₹4,899 / 4,161 / 2,888 | 19 / 66 / 171 |
| Shared loads | ₹21,507 / 22,386 / 22,598 | ₹4,415 / 3,474 / 2,759 | 19 / 65 / 140 |
| **Shared + return loads** | ₹21,497 / 22,352 / 21,861 | ₹4,393 / 3,430 / 2,550 | 19 / 67 / 180 |

**Readings**
1. **Where it helps.** Sharing pays when lots are clustered: 4 shared loads per medium day, and 13 trucks instead of
   17. Return loads pay when trucks are scarce: 10–20 extra lots moved per dense day. When lots are few and spread
   out, both barely matter: +2.4%, and only 14 of 40 days improve.
2. **Discount the dense headline.** Most of the +38% to +47% is moving about twice as many lots with the same trucks.
   It is scored as though a lot left at the farm earns nothing, when really it would be sold locally or later at
   some price. The per-tonne rows are the robust part: shared loads cut transport cost per tonne by 11–18% and raise
   net value per tonne by 1–3%.
3. **Return loads raise the total but lower value per tonne** (dense: ₹21,861/t vs ₹22,598/t for shared loads
   alone). The extra lots are the ones the plan couldn't place well, carried on the way home. They are still worth
   it, and none break a limit.
4. **Rarely worse.** On 2 of 240 days, shared + return loads realised less than V3-0: it planned better, but the
   realised prices moved against it. It is never worse on its own planning objective (`tests/test_loads.py`).
5. **Assumptions that matter most here:**
   - A same-day second sale at the realised price. Late arrivals may get worse prices.
   - 20 minutes per extra pickup.
   - A 12-hour driver day.
   - Pickups within 20 km. Trips still track one pickup point, so wider groups would make geofences wrong
     (backlog 9).

## Product

- **Farmer best-mandi (V3-0):**
  - `/recommend/best-mandi` goes through the optimizer: the truck cost model, the two hard limits, and room net of
    trucks already on the way. Options that break a limit are listed last, with the reason.
  - `[recommender] default = "rule"` brings V1 back.
- **Admin → "Recommenders: V1 rule vs optimizer" (V3-0):** both methods on a simulated batch against today's
  forecasts. It warns when mandis have no arrivals history, since then no mandi-room limit can be applied.
- **FPO → "Plan shared truckloads" (V3-1):**
  - Proposes shared hired-truck loads for the FPO's own waiting lots: lots in pickup order, mandi, truck size, and
    the saving vs one truck per lot.
  - **Accept** creates one shipment per load through the normal checked path. The pickup order and proposal id go
    into the audit log.
  - If a lot changed after the proposal was made, accept fails and the proposal is marked **stale**.
- **Fleet owner → "Return loads" (V3-1):**
  - For this fleet's trucks that reached their mandi today: shipments already booked with **this** fleet that have
    no trip yet.
  - Shows empty km saved and the driver's day. **Accept** assigns the same truck and driver (audited).
  - A truck's home is taken as its delivering trip's start, because vehicles have no stored base.
- Proposals live in `load_proposals` (migration 0011): proposed / accepted / rejected / stale, with who decided and
  why. They are tenant-scoped; another org's proposal returns 404.
- Switches: `[consolidation] enabled`, `[return_loads] enabled` in `config/recommender.toml`. Tests pin them to the
  study's verdict.

## Reproduce

```
python -m agripulse_ml.decision_study         # V3-0, ~45 min first time (forecasts then cached in data/cache/)
python -m agripulse_ml.consolidation_study    # V3-1, ~50 min with cached forecasts
pytest tests/test_decisions.py tests/test_loads.py
```

## Known issues

- Mandi price impact of oversupply is not modelled; it is counted as a violation only (backlog 23).
- Unshipped lots count as ₹0, which overstates gains for plans that ship more (V3-1 dense). The per-tonne table is
  the conservative view (backlog 26).
- Dense V3-0 solves sometimes stop at the work limit (19/80), so those plans are good but not proven optimal
  (backlog 24).
- A shared load becomes one shipment, and its trip still uses a single weighted pickup point, so multi-stop
  tracking and geofences are approximate (backlog 9).
- Distances are approximate in the studies; production uses OSRM when `OSRM_URL` is set.
- Truck rates, the spoilage cap, the mandi-room share, the loading time and the driver day are assumptions; replace
  them with fleet quotes and field data.
