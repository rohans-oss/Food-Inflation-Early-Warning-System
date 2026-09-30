# AgriPulse: pitch deck outline (V3-4)

Twelve slides, about 8 minutes. Each slide has its message (the one thing the audience should remember), the content,
a suggested visual and speaker notes. Every number is either labelled SYNTHETIC or comes from real data, and says
which. **Rule for the talk: never say "the model predicts" without saying on what data.**

---

### 1. Title
- **Message:** AgriPulse sees tomato supply moving and says honestly how much it can forecast.
- **Content:** "AgriPulse: food-inflation early warning and farm-to-mandi tracking for tomato, Karnataka." Name, programme, date.
- **Visual:** the Policy map with mandis and a live (simulated, labelled) truck.
- **Notes:** one line on why tomato: the most volatile item in India's vegetable basket.

### 2. The problem
- **Message:** Tomato prices jump faster than anyone can react, and farmers sell blind.
- **Content:**
  - In 2023, India's average retail tomato price went from ₹24.68/kg to ₹108.92/kg by 11 July, up 341% (Department of
    Consumer Affairs data, via CNBC).
  - Official mandi data arrives a day late, and it tells you nothing about supply already on the road.
  - A farmer choosing a mandi weighs price against transport and spoilage with no ranges and no warning.
- **Visual:** one line chart of the 2023 retail price, with a source line.
- **Notes:** keep it to the one documented episode; do not generalise to "every year".

### 3. The gap
- **Message:** Existing tools give point prices after the fact. Nobody joins forecasts, logistics and physical supply.
- **Content:** Agmarknet publishes prices a day later. Forecasts, where they exist, are single numbers. Trucks
  in transit are invisible to policy.
- **Visual:** a three-column "today vs AgriPulse" table.

### 4. The platform: 9 roles, one chain
- **Message:** It is a working product, not a notebook.
- **Content:** farmer, FPO, transporter/driver, fleet owner, trader, bulk buyer, policy analyst, lender/insurer, admin.
  Each has a real login, access control and a working screen. The chain: lot → shipment → trip → QR pickup → live
  tracking → QR delivery → weighing → verified history.
- **Visual:** the lifecycle as a strip of 6 screenshots.
- **Notes:** mention multi-tenancy (FPOs and fleets see only their own data) and privacy (tracking only during a
  trip, with consent, a visible indicator, and expiring public links).

### 5. V1: what was built
- **Message:** Forecast ranges plus live supply tracking, end to end.
- **Content:**
  - Daily Agmarknet and weather ingest, with data cleaning that flags bad rows (the real feed has them).
  - Quantile forecasts 1–4 weeks ahead (p10 / p50 / p90) plus a spike probability.
  - Walk-forward backtest against naive.
  - Driver app with background GPS and offline buffer, geofences, ETA, alerts in English and Kannada (now Hindi too).
- **Visual:** the farmer lot page (best-mandi ranges + live truck).
- **Demo:** play `tejas.mp4`, or its first minute.

### 6. V2: we tried to beat "today's price", and couldn't (yet)
- **Message:** A negative result, reported honestly, is a strength: it tells us what not to ship.
- **Content:**
  - A shared evaluation harness: same folds, same metrics for every model.
  - 8 synthetic draws, not 1. V1's "LightGBM beats naive" held on one draw only; across 8 there is no reliable edge.
  - TFT: 15–33% worse than naive. Graph neural network: 25–32% worse. Graph *features*: +1–3%, only to parity
    (TFT and graph models on 3 draws).
  - All SYNTHETIC — METHODOLOGY DEMO.
- **Visual:** a bar chart of "% better than naive" per model, with the zero line prominent and the SYNTHETIC badge.
- **Notes:** "We built the ruler before building the models. The ruler says no model has earned trust yet."

### 7. V2: real data where it exists
- **Message:** Where real data exists, we used it, and it also said no.
- **Content:**
  - 2,610 real Sentinel-2 observations for the pilot districts.
  - District cropland NDVI does not track tomato area or production beyond a shared trend: r = 0.60 falls to −0.14
    once detrended (2 districts, n = 10). The NDVI signal itself is sound; the tomato link is not there at that scale.
  - Found and fixed along the way: satellite scenes with a double-applied offset that gave NDVI up to 1.93.
- **Visual:** NDVI vs tomato area, raw and detrended.

### 8. Pre-V3 hardening
- **Message:** Fix what users would trip on before adding features.
- **Content:**
  - **Calibration.** The 80% range held the price only 74–79% of the time. After calibration it holds 79–81%, the
    target, averaged across the same 8 datasets (SYNTHETIC).
  - **Driver app.** Browsers stop GPS when the screen locks, so an Android app now tracks in the background. It builds
    in CI but has not been field-tested.
  - **Sessions.** Logins can be revoked per device.
- **Visual:** coverage before/after per horizon.

### 9. V3: decisions under uncertainty
- **Message:** The intelligence is in the decisions (cost, capacity, spoilage, uncertainty), not in a better price guess.
- **Content** (240 simulated days, SYNTHETIC):
  - **OR-Tools optimizer vs the V1 rule.** Net value a tie (+0.2%), mandi-overload violations 140 → 0, transport cost
    −22 to −34%. It is now the default.
  - **Shared truckloads and return loads.** Better than the optimizer alone on 204/240 days, worse on 2. Per tonne
    shipped: transport −11 to −18%, net value +1 to +3%.
- **Visual:** the Admin "rule vs optimizer" table, plus the FPO shared-load proposal.
- **Demo:** first 45 s of `decisions.mp4`.
- **Notes:** say out loud that the dense-batch gain is mostly shipping more lots, scored generously.

### 10. V3: the scenario simulator
- **Message:** "What if the rain fails?" answered two ways, never blended, always labelled.
- **Content:**
  - The sourced assumption chain: RBI elasticity −0.72, FAO yield-water response. A 50% June–July rain deficit in
    Kolar/Chikkaballapur gives about +10% (range +0.6 to +49%). An export ban gives −0.3%, because exports are only 0.47%
    of production.
  - The current model's own answer is the wrong sign, and we show that.
  - Every screen says **COUNTERFACTUAL ESTIMATE — not a validated causal model**.
- **Visual:** the simulator map and the two-channel table.

### 11. What is proven, and what isn't (rule 24)
- **Message:** One honest table.
- **Content:** a three-colour table:
  - **Real:** road distances; the satellite pipeline and its negative finding; ingest and cleaning on the real feed;
    the tracking pipeline, in tests and the demo.
  - **Synthetic only:** every forecast comparison, calibration, optimizer and load gains.
  - **Not yet provable:** the real spike backtest (17 real price rows today, first fold possible 2027-10-24 without a
    backfill), the transit feature's value, the Android app's GPS gaps, decisions on real sales.
- **Visual:** the table from `docs/final-evaluation.md`.

### 12. Roadmap to real results
- **Message:** Every open question has a date or a trigger, and the harness is ready.
- **Content:**
  1. Backfill Agmarknet history (the 2023 spike included), then run the real spike backtest the same day.
  2. Field test with the Android app, to measure GPS gaps and the lifecycle on real roads.
  3. Native-speaker review of Kannada and Hindi; confirm the 18 mandi locations.
  4. About 13 months of daily data (to ~2027-10): re-run every model comparison on real prices; the default model
     changes only if real data says so.
- **Ask:** pilot FPO partners, a native Kannada reviewer, and access to APMC arrival records.
- **Visual:** a timeline.

---

**Backup slides:** the leakage tests (the planted-leak checks), the privacy design, the stack diagram, and
the per-draw variance tables.

Sources for slide 2: CNBC, 13 July 2023, "India's tomato prices surge over 300%", quoting Department of Consumer
Affairs data (<https://www.cnbc.com/2023/07/13/indias-tomato-prices-surge-over-300percent-prompting-thieves-and-turmoil.html>).
Slide 10: see `docs/scenario-assumptions.md` for each source.
