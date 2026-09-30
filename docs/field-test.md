# Field test: a real phone on a real route

This is the V1 done-criterion that no automated test can tick: *a real phone drives a real route while a farmer
watches live with ETA, geofence events and delivery confirmation.* Budget half a day.

## 0. Before you leave (at a desk)

- [ ] The stack runs on a server with a domain and **HTTPS** (`docs/deployment.md`). Phone browsers **refuse GPS on
      plain http** pages (except `localhost`), so a laptop IP like `http://192.168.1.5:8000` will not work.
      No server? Run a tunnel from your laptop instead: `cloudflared tunnel --url http://localhost:80` behind the
      prod overlay, and use the https URL it prints.
- [ ] `ADMIN_PASSWORD` and `JWT_SECRET` are set; demo users are **off** (prod overlay does this).
- [ ] Mandi coordinates for the route's destination are **verified** (Admin → confirm on a map, then
      `PATCH /admin/mandis/{id}` with `lat`, `lon`, `coords_verified: true`). The geofence is a 500 m circle around
      that point; a wrong point means `reached_mandi` never fires. Increase `geofence_radius_m` for big yards.
- [ ] OSRM is running (`docs/routing.md`), or accept the "approximate route" badge and a looser ETA.
- [ ] Create real accounts: 1 farmer, 1 FPO, 1 fleet owner, 1 driver (joins the fleet, owner approves),
      1 trader at the destination mandi. Five people, or two people with five browsers.
- [ ] The fleet owner has added the real vehicle (registration as on the plate).
- [ ] A forecast exists for the destination mandi (`python -m agripulse_ml.predict` after training), otherwise the
      recommender step shows nothing.

## 1. Phone set-up (driver)

- [ ] **Preferred: the Android app** (`docs/driver-android.md`). Install the APK from the `driver-android` CI run,
      allow location "While using the app" and notifications, and set battery to Unrestricted. Location then
      continues with the screen locked, during calls and with Maps in front.
- [ ] Browser fallback: Android + Chrome. iOS Safari works but pauses GPS the moment the screen locks.
- [ ] Browser only: open `https://<domain>/api/driver/`, sign in, **Add to Home Screen**, open it from the home-screen icon.
- [ ] Allow location ("while using the app") and camera.
- [ ] Settings → Battery → set the browser to **unrestricted / no optimisation** (Android kills background tabs otherwise).
- [ ] Plug the phone into the vehicle charger. Wake lock + GPS drains ~15–25%/hour.
- [ ] Mount it where it can see the sky; keep the app **open on screen** for the whole trip.

## 2. The run

| Step | Who | What to check |
|---|---|---|
| Register lot | Farmer (web) | Pickup point set by "Use my location" at the actual loading spot |
| Best mandi | Farmer | Table shows ranges; note which mandi ranks first and why |
| Group + book | FPO | Shipment created for the destination mandi; fleet booked |
| Assign | Fleet owner | Real vehicle + driver |
| Accept → consent → start | Driver (PWA) | Red **TRACKING ON** bar appears only after consent + start |
| Pickup QR | Driver scans farmer's screen | Farmer gets "picked up" alert with the live link |
| Drive | — | Farmer's lot page: marker moves, ETA updates, "Left pickup area" appears |
| Tunnel / dead zone | — | Toggle airplane mode for 2–3 min mid-route: the PWA shows "N queued"; on reconnect the track fills the gap |
| Stop for 35 min (optional) | — | `unexpected_stop` event + alert to fleet owner / FPO |
| Arrive | — | `reached_mandi` fires inside the geofence; trader + farmer alerted |
| Delivery QR | Trader scans driver's phone | Lot moves to "at mandi" |
| Weigh + price | Trader | Farmer sees weight, price, value; FPO sees payout pending |
| End trip | Driver | Tracking bar disappears; no more points accepted |
| Verify | Lender | Lot shows pickup → route → delivery with a reliability score |

## 3. Write down (docs/field-test-results.md)

- Date, route, distance, phone model + OS, **Android app (APK run number) or browser**, network (Jio/Airtel/…)
- With the Android app: lock the screen for 10 min and switch to Maps for 10 min mid-route. Did fixes keep arriving?
  (This is what B-2 option (b) has to prove.)
- GPS fixes received vs expected (`points_count` in `GET /trips/{id}`; expected ≈ trip seconds / 5–10)
- Longest gap between fixes and why (screen locked? dead zone?)
- ETA error at 30 / 15 / 5 min before arrival (predicted vs actual)
- Did `reached_mandi` fire, and how far inside the yard?
- Battery used
- Anything that confused the driver

That results file is the evidence for the "real tracking" claim. Without it, say "tested in simulation only".

## 4. Should the driver app become React Native?

**Decided in Pre-V3 B-2: no.** It is a Capacitor wrapper around the same code instead (`docs/driver-app-investigation.md`,
`docs/driver-android.md`). The reliability comes from the native foreground service, which Capacitor provides too.
The notes below were the original decision rule:

- **Stay on the PWA** if the phone stays unlocked on a mount and gaps are only from dead zones (the offline buffer covers those).
- **Switch to React Native** (with a background-location library and a foreground service on Android) if drivers
  lock the phone, answer calls, or switch apps. Browsers stop geolocation in those cases and there is no web API
  that fixes it. The Background Geolocation API is not available to web pages.

The server side does not change either way: RN posts to the same `/trips/{id}/points` and `/ws/driver/{id}`.

---

# 3-minute demo video script (Tejas's journey)

Record the screen at 1080p. Pre-seed: trained model, verified Kolar APMC location, simulator **off** for the
real-trip segment. Use two browser windows side by side (farmer | driver phone mirror) where noted.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:15 | Policy map | "Tomato prices in India swing 3–5× in weeks. AgriPulse forecasts them as ranges and watches supply physically moving toward mandis." |
| 0:15–0:40 | Farmer: register lot | "Tejas has 2 tonnes. He drops a pin at his farm." Show the nearby-prices table. |
| 0:40–1:05 | Farmer: best mandi | "Not just a price: net value after transport and spoilage, with a range. When ranges overlap, we say so." Point at the forecast chart vs the naive baseline. |
| 1:05–1:20 | FPO → Fleet | Group into shipment, book fleet, assign KA-01-XX-1234. (Cut quickly.) |
| 1:20–1:40 | Driver phone | Accept, consent screen, TRACKING ON, scan the farmer's QR. "Tracking exists only during a trip, with consent, and it's visible." |
| 1:40–2:10 | Farmer lot page (real footage) | "Vehicle is 42 km away, arriving 3:40 PM." Show the public link opened with no login. |
| 2:10–2:30 | Trader board | Incoming truck, expected-today vs normal. "Every tracked truck is supply the official data will only report tomorrow." Scan delivery QR, weigh. |
| 2:30–2:45 | Farmer + Lender | Delivery confirmation; lender sees the verified chain and reliability score. |
| 2:45–3:00 | Admin | Freshness panel and the backtest table. "Walk-forward backtest against a naive baseline." If the model is still synthetic, **say so on camera**. |

Rules for the video: any simulated truck on screen must show its **Simulated** badge; don't crop it out.
