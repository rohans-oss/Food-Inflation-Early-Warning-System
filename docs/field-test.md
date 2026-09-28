# Field test and demo script (V1 "done" criteria 3 and 5)

A real phone drives a real route while a farmer watches it live. This is what makes V1 honest, and it has to be done
by people outdoors: code alone can't tick this box. Record the result at the bottom of this file.

## Before the day

- [ ] Deploy with a public HTTPS URL for both the API and the web app. Phone geolocation and camera need HTTPS.
      Set `PUBLIC_BASE_URL` (web), `NEXT_PUBLIC_API_URL` (API, then rebuild web) and `CORS_ORIGINS`.
- [ ] OSRM running and `OSRM_URL` set (docs/routing.md), so ETAs use road distance, not the fallback.
- [ ] Set the destination mandi's coordinates and geofence radius from a site visit (Admin → Data quality → set). Seeded coordinates are approximate.
- [ ] Accounts: one real farmer (or a teammate playing Tejas), FPO desk, fleet owner, driver (on the test phone), trader.
- [ ] Driver phone: Android + Chrome. Open `https://<api>/driver/`, *Add to Home screen*, allow location ("while using")
      and camera. Keep the screen on (the app requests a wake lock) and turn off battery optimisation for Chrome.
- [ ] The model is trained on real history, **or** every screen shows the "Synthetic data" badge and you say so out loud.

## On the day

| # | Who | Action | Expect |
|---|---|---|---|
| 1 | Farmer (web, phone or laptop) | Register a lot: tomato, tons, grade, *Use my location* at the actual pickup point, choose the FPO | Lot appears; best-mandi table ranks mandis with p10/p50/p90 net value |
| 2 | FPO (web) | Group the lot → destination mandi → book the fleet | Shipment `booked` |
| 3 | Fleet owner (web) | Assign vehicle + driver | Trip appears on the driver phone |
| 4 | Driver (PWA) | Accept → tick consent → Start | Red "Tracking ON" banner; farmer's screen shows the trip and a share link |
| 5 | Driver | Scan the pickup QR on the farmer's screen | `picked_up` event; farmer gets an alert with the tracking link |
| 6 | Farmer | Open the share link on another phone with no login | Live dot moves, "X km away, arriving HH:MM" |
| 7 | Driver | Drive. Put the phone in airplane mode for ~5 min, then back on | Points are buffered and flushed; no gap in the track after sync |
| 8 | – | Enter the mandi geofence | `reached_mandi` event; farmer, FPO and trader get "vehicle arrived" |
| 9 | Trader (web, phone) | *Confirm arrival* → scan the delivery QR on the driver's phone → record weight and price | Lot `delivered`; farmer gets delivery confirmation with weight, price and payout status |
| 10 | Driver | End trip | Share link expires within 2 h; tracking stops |
| 11 | FPO | Mark payout paid | Farmer sees `paid` |
| 12 | Lender (web) | Open the lot | Full chain: pickup QR → route → delivery QR → weight, reliability score |

Record the screen during steps 1–12 (farmer view plus a phone camera on the road) for the 3-minute demo video.

## Known limits to say out loud

- Browser background GPS: Chrome suspends pages that are off-screen. Keep the PWA in the foreground with the screen on.
  If that's unreliable in practice, switch the driver app to React Native (project doc, risks table).
- In-transit tonnage counts tracked trucks only, so it is a lower bound.
- Any simulated trucks on screen are labelled "Simulated". They are not part of the field test.

## Results log

| Date | Route | Phone / browser | GPS points | Gaps after offline test | ETA error at 50% of route | Events fired | Notes |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
