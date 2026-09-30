# Scenario simulator: assumptions and what the answers mean (V3-2)

> **COUNTERFACTUAL ESTIMATE — not a validated causal model.** Every scenario output carries this label in the API,
> the UI and this document (rule 22). Today the forecasts it shifts are **SYNTHETIC** (trained on synthetic
> prices), so every result is also labelled SYNTHETIC. The simulator is machinery that works, not a finding about
> real tomato prices.

## What it does

The simulator takes the **display-model forecast users see** (V1 LightGBM, B-1 calibrated) and asks: *if this
shock happened, how would the 1–4 week p10 / p50 / p90 range move?* It answers in **two separate channels, which
are never blended**:

| | (B) Documented assumption chain | (A) What the current model does |
|---|---|---|
| How | Supply shock → price, through a sourced demand elasticity, with low / central / high ranges | Change the model's own raw inputs (rain, arrivals), rebuild its features, re-run it |
| Tells you | What the published numbers imply | How *this* model reacts: a sensitivity of the model |
| Validity | As good as the assumptions below. Two links are unsourced. | The model learned from synthetic data in which only **excess** rain drives spikes and arrivals **follow** price (`ml/agripulse_ml/synthetic.py`). Its answer is a property of that data, not of tomato markets. |
| Sees | Any window; effects land after a harvest lag | Rain only through its last 30 days (`rain_7`, `rain_30`, `rain_30_anom`); arrivals only through the last 7 (`arrivals_7_rel`) |

Runs are stored in `scenario_runs` (migration 0012) with the parameters, the assumptions used (including sources),
both channels' results, the model and forecast date, and who ran it. **The `forecasts` table is never written**;
`tests/test_scenarios.py` compares it byte for byte before and after.

## Assumptions (`config/scenarios.toml`)

### Price response to supply (both scenarios)

| Value | Used | Source |
|---|---|---|
| Elasticity of tomato price to availability | **−0.721** central; −0.357 to −1.085 (± 1 standard error, SE 0.364) | RBI Working Paper (DEPR) 08/2024, Roy, Gupta, Wardhan, Sarkar, Tewari, Bansal, Bhatia and Gulati, *Vegetables Inflation in India: A Study of Tomato, Onion and Potato (TOP)*, 3 Oct 2024. ARDL long-run coefficient of log availability-usage ratio on log CPI-tomato, monthly, Jul 2012 – Jun 2022. |

Caveats:
- This is a **retail CPI** response. Wholesale mandi prices usually move more, so channel B probably understates
  the mandi-level shift.
- The coefficient is national. It is applied per mandi.

Formula: `price multiplier = (1 + supply change) ^ elasticity`.

### Rainfall failure

```
supply change (affected mandi) = − ky × rain deficit × unreplaced-rain share × local-supply share
affected arrivals: from (window start + 60 d) to (window end + 140 d)
```

| Value | Used | Source |
|---|---|---|
| Tomato yield response to water deficit, ky | **1.05** central; 0.4 to 1.1 | FAO crop information, tomato: seasonal ky 1.05; 0.4–1.1 across growth stages. |
| Share of the crop's water need that comes from rain and is **not replaced by irrigation** | 0.4 central; 0.2 to 0.7 | **UNSOURCED.** Kolar/Chikkaballapur tomato is largely borewell-irrigated, so a rain deficit is only partly a water deficit. Needs agronomy / district irrigation data. |
| Share of a mandi's arrivals that come from its own district | 0.6 central; 0.4 to 0.8 | **UNSOURCED.** Needs trader or APMC origin data. |
| Harvest lag | Arrivals hit from +60 days after the window starts to +140 days after it ends | FAO season length of 90–140 days after transplanting. Harvests starting at about 60 days is an assumption. |

Mandis outside the chosen districts are not shifted. Supply from elsewhere is assumed unchanged, which ignores
traders redirecting produce.

### Export ban

| Value | Used | Source |
|---|---|---|
| India's tomato exports (HS 070200, fresh or chilled), 2023 | 96,801.9 t (Bangladesh 48.2 kt, Nepal 25.5 kt, UAE 11.5 kt, Bhutan 3.6 kt, Maldives 2.3 kt) | World Bank WITS / UN Comtrade |
| India's tomato production, 2023-24 | 208.19 lakh t (first advance estimate) | PIB, Ministry of Agriculture & Farmers Welfare |
| Export share | **0.47%** of production | The two rows above. The RBI study above: "tomato exports account for less than 1 per cent of total production". |

Formula: `supply change = + export share` from the ban's start date. A user may enter a regional share (0–25%), for
example for a border state; the run records it as `share_is_user_assumption: true`.

## What the simulator says today (synthetic-trained model, 18 mandis, forecasts issued 2026-09-25)

These are p50 shifts in the affected mandis, at 1 / 2 / 3 / 4 weeks. They come from a one-off check run with the
display model trained on the synthetic history (2024-01 to 2026-09).

| Scenario | (B) assumption chain | (A) current model |
|---|---|---|
| Total rain failure over the last 30 days, all districts | 0 / 0 / 0 / 0% (arrivals are hit only after the harvest lag, beyond 4 weeks) | **−17.7 / −14.9 / −3.8 / +25.0%** |
| 50% rain deficit over the last 30 days, Kolar + Chikkaballapur | 0 / 0 / 0 / 0% | −13.9 / −11.5 / +0.1 / −0.0% |
| 50% rain deficit Jun–Jul (harvest arriving now), Kolar + Chikkaballapur | **+10.2%** at every horizon (range multiplier 1.006–1.49) | 0% (outside the model's 30-day view) |
| Export ban, national share (0.47%) | **−0.33%** | +0.22 / +0.15 / +0.08 / +0.18% (**wrong sign**) |
| Export ban, user share 10% | −6.6% | **+2.3 / +4.3 / +2.2 / +2.4% (wrong sign)** |

**Reading:**

1. **The model channel is not credible for these shocks.**
   - A total drought makes this model predict *lower* prices for 1–3 weeks, then *higher* at 4 weeks. The sign flips
     between horizons.
   - More supply from an export ban makes it predict *higher* prices.
   - Both follow from its synthetic training data: dry spells meant no spikes, and arrivals followed price rather
     than caused it. Extreme input values also push its trees outside anything they saw in training.
   - The UI therefore flags "channels disagree" whenever A and B point in opposite directions.
2. **The assumption chain gives the textbook direction**, with honest ranges. A 50% deficit in a heavily irrigated
   belt gives about +10%, anywhere from +0.6% to +49%, because two of its links are unsourced.
3. **An export ban barely matters for tomato nationally** (−0.3%), because India exports under 1% of its tomatoes.
   This is itself a useful policy answer, not a failure of the tool.
4. **Timing matters.** A deficit happening now shows up in arrivals two months or more later, beyond the 4-week
   forecast horizon. The tool says so rather than inventing an immediate effect.

## Using it

Go to Policy → **Scenario simulator**. Pick the scenario and its settings, then run it. The screen shows:
- a map of mandis coloured by p50 shift (red up, blue down; the same values as text in the table);
- baseline vs scenario ranges per mandi, for either channel and any horizon;
- the assumptions used, with their sources.

API: `GET /scenarios`, `POST /scenarios/run`, `GET /scenarios/runs`, `GET /scenarios/runs/{id}` (Policy and Admin).

## Before relying on any result

- Replace the two **UNSOURCED** links with data: the irrigated share of tomato area and water use (district
  agriculture office / Minor Irrigation Census), and mandi arrival origins (APMC gate records, traders).
- Retrain on **real** prices (about 13 months from 2026-09-25) before reading anything into channel A. Even then it
  can only react to shocks that occurred in its training history.
- A mandi-level (wholesale) elasticity estimated from real Agmarknet prices and arrivals would replace the retail
  CPI number. This is the same estimate the optimizer needs (backlog 23).
