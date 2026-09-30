# Driver app: GPS gaps when the screen locks (Pre-V3 B-2, investigation only)

**Status: investigation. No app code has changed.** The fix is chosen by the project owner.

**Evidence level.** There is no field-test data yet (`docs/field-test-results.md` does not exist). The numbers
below come from two simulations:

1. The real `apps/driver-pwa/app.js`, run in headless Chromium (`b2-investigation/pwa_lock.py`).
2. The real tracking engine and monitor, replaying one trip under four driver behaviours
   (`b2-investigation/test_gap_replay.py`, results in `replay.json`).

The behaviour timings (when calls happen, how long stops last, whether the driver uses Maps) are **assumptions**.
The field test is what measures them.

## 1. What the code does today

- `watchPosition` records a fix about every 5 s. Every fix goes into IndexedDB first, then is flushed over
  WebSocket or HTTP. Dead zones therefore lose nothing: fixes are recorded and sent later.
- A screen wake lock is requested when tracking starts.
- **Bug found:** the wake lock is never re-acquired after the page has been hidden once.
  - The browser releases the wake lock automatically whenever the page is hidden (a call, the power button,
    switching apps). The `wakeLock` variable still holds the released object, so `if (!wakeLock)` on
    `visibilitychange` is false and no new lock is requested.
  - After the first interruption the phone's normal auto-lock timeout applies for the rest of the trip.
  - Confirmed in Chromium with a spec-accurate Wake Lock:

    | | after start | after lock + unlock | after second lock + unlock |
    |---|---|---|---|
    | lock held | yes | **no** | **no** |
    | requests made | 1 | 1 | 1 |

- **Why GPS stops when the screen locks.** Browsers stop delivering geolocation to hidden pages. This is by
  design: Chromium issue 41186218 and Firefox bug 1771229 ("stops listening for location updates when the app is
  backgrounded"). No web API lets a page collect location while hidden; the Background Geolocation proposal is
  not shipped. When the page is hidden no fix is recorded at all, so the offline buffer has nothing to replay and
  the gap is permanent.
- **Limit of this simulation.** Headless desktop Chromium does not suspend hidden pages the way a locked phone
  does: timers kept running in the test. The "no fixes while hidden" behaviour is therefore taken from the
  documented browser behaviour above, not reproduced in this container.

## 2. Impact: one 55 km farm → Bengaluru trip through the real engine

The trip is about 114 minutes to arrival, including a 20-minute tea stop, with a fix every 5 s. Every scenario
also has two 3-minute dead zones.

| | A: screen on, mounted | B: **current app**, one 2-min call | C: wake lock fixed, 2 calls + locked at tea stop | D: driver runs Maps navigation |
|---|---|---|---|---|
| Fixes recorded / expected | 1488 / 1488 | 325 / 1488 | 1176 / 1488 | 24 / 1488 |
| Fixes lost | 0% | **78%** | 21% (most while parked) | **98%** |
| Driving minutes with no fix | 0 | 69 | 6 | 93 |
| Longest gap | 5 s | 49 min | 20 min | 122 min |
| Minutes the farmer's view was > 2 min stale | 2% | 76% | 18% | 98% |
| Route km drawn as a straight line | 0 | 41 | 4 | 55 |
| False "unexpected stop (no signal)" events | 0 | 2 | 0 | 1 |
| Stop / delay alerts sent to fleet owner and FPO | 0 | 4 | 0 | 2 |
| `reached_mandi` late by | 0 min | 9 min | 0 min | 9 min |

In B and D the driver opens the app about 10 minutes after arriving, to show the delivery QR.

Reading:
- **Dead zones are solved** (A).
- **The wake-lock bug is the biggest single problem in the current app** (B). One phone call turns the rest of
  the trip into a gap, and the driver never sees a warning.
- **Fixing the bug gets reasonable coverage (C), but only if the driver keeps the app on screen.** A call or a
  locked phone at a stop still loses those minutes.
- **No PWA change helps D.** Many drivers navigate with Google Maps. If Maps is in the foreground, the PWA is
  hidden and records nothing.
- The server-side consequences are wrong alarms: `unexpected_stop` with reason `no_signal`, ETA pushed later by
  the monitor, delay alerts. A late `reached_mandi` delays the trader's and farmer's arrival alerts.

## 3. Mitigations short of a rewrite

| Mitigation | What it fixes | What it doesn't | Effort | Reliability gain |
|---|---|---|---|---|
| **Re-acquire the wake lock on every return to visible** (listen for `release`, reset the variable, re-request) | B → C: auto-lock after a call | Power button, calls while they last, Maps in front | 0.5 day incl. test | Large for the current app (78% → about 21% lost in the model) |
| **Gap awareness:** on return to visible, tell the driver "tracking paused N min while the screen was off"; send a gap marker so the server labels it "phone screen off" rather than "no signal"; farmer view shows "location paused since HH:MM" | Wrong alarms and silent staleness; honest UI (rule 4 spirit) | Doesn't recover any location | 1–1.5 days | No extra fixes; removes misleading alerts |
| **"Keep screen on" instructions:** checklist before start (mount, charger, don't press power, battery unrestricted) | Driver behaviour, partly | Calls, Maps | 0.5 day | Depends on the driver; unmeasured |
| **Android split-screen** (Maps + PWA both visible) | D in principle: a visible page keeps GPS | Awkward on a phone; unverified on real devices | 0 code (instructions) | Unknown; needs a device test |
| **Background Sync / Periodic Background Sync** | Nothing here. They can resend queued data but cannot collect GPS; Periodic Sync runs at most every few hours | Everything | — | None |
| **Capacitor wrapper** (native shell around the same web code, plus a background-geolocation plugin running an Android foreground service) | B, C, D, power button: location keeps flowing while locked or while Maps is in front | OEM battery killers (Xiaomi / Oppo / Vivo / Realme) unless the driver exempts the app; iOS needs an Apple account | see (b) | Large; needs field confirmation on the phones drivers actually use |

## 4. Options with effort

**(a) Lightweight in-PWA fix: 2–3 days.**
- Wake-lock re-acquire: 0.5 day.
- Gap awareness, client and server: 1–1.5 days.
- Pre-trip checklist: 0.5 day.
- Tests: 0.5 day.

Result: fixes the current app's worst failure (B) and makes gaps honest. It does not fix calls, a locked phone
or Maps navigation. That is a browser limit, not a code issue.

**(b) Capacitor wrapper around the existing PWA: Android 6–8 days, plus 3–4 days for iOS.**
Android work:
- Project and plugin set-up (`@capacitor-community/background-geolocation`, maintained, Capacitor 7): 1 day.
- Route the tracking calls to the plugin, keeping the web API as the fallback: 1–1.5 days.
- Native HTTP flush: the WebView's HTTP is throttled after about 5 minutes in the background, per the plugin
  README: 1 day.
- Permission and battery-optimisation flow, including Android 13+ notification permission and the Android 14
  `FOREGROUND_SERVICE_LOCATION` foreground-service type: 1 day.
- Signed APK and distribution (sideload for the pilot; Play Store later needs the foreground-service
  declaration): 0.5–1 day.
- Testing on 2–3 real phones from different makers: 1.5–2 days.

The server API stays the same. The foreground-service notification is itself a visible "tracking on" indicator
even when the screen is locked, which is better for privacy rule 4 than the in-page bar. A foreground service
counts as foreground location access, so the app does not need `ACCESS_BACKGROUND_LOCATION`.

**(c) Full React Native rewrite: 12–18 days for Android + iOS.**
- Rebuild every screen: login and refresh, trip list, consent, QR scan and display, offline queue, WebSocket.
- Add a background-location library. The most robust one, Transistorsoft, needs a paid licence for Android
  release builds.

This gives the same reliability as (b): the gain comes from the native foreground service, not from React Native.
It also means a second codebase to maintain.

## 5. Recommendation

**(b) Capacitor, Android first, with (a)'s wake-lock fix and gap awareness included.** Those two are needed
anyway for anyone still using the browser version.

- Scenario D (driver navigating with Maps) is likely for truck drivers, and only native code fixes it.
- (b) reuses the existing app code, and the server doesn't change.
- (c) costs roughly twice as much for no extra reliability.
- If you want to spend less first: do (a) (2–3 days), run the field test, and move to (b) only if the field
  test shows locked phones or Maps use. This risks one more field trip.

The one thing that could change this: if the field test shows drivers keep the app on screen, (a) alone is enough.
