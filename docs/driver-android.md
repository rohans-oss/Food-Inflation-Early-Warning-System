# Android driver app (Pre-V3 B-2, option b)

`apps/driver-android` wraps the driver PWA (`apps/driver-pwa`, the same files) in a native Android shell
built with Capacitor 7.

The difference from the browser version is where location comes from. In the Android app it comes from
`@capacitor-community/background-geolocation`, which runs an Android **foreground service**. Location keeps
flowing when:
- the screen is locked,
- the driver is on a call,
- Google Maps is in front.

The browser version cannot do this (see `docs/driver-app-investigation.md`). The server API is unchanged.

**Status: built in CI, not yet tested on a real phone.** The field test is what confirms it (`docs/field-test.md`).

## Privacy (rule 4)

- Location runs **only** between "Start trip" (after the consent tick) and "End trip". It also stops on:
  - withdrawn consent,
  - logout,
  - the server refusing points because the trip ended or consent was withdrawn elsewhere.
- An ongoing notification, **"AgriPulse: tracking on — Sharing your location for trip #N until you end the
  trip"**, is shown the whole time. It works with the screen locked, unlike the in-app red bar.
- The app asks for notification permission (Android 13+) so that this notification is actually visible.
- It asks for location **"While using the app"** only. A foreground service counts as foreground access, so
  `ACCESS_BACKGROUND_LOCATION` is not requested.

## Get the APK

The debug build is fine for a pilot.

1. On GitHub, go to **Settings → Secrets and variables → Actions → Variables**. Add `AGRIPULSE_API` set to the
   API's https address, for example `https://agripulse.example.org/api`. It must be https: phones refuse location
   on plain http.
2. Go to **Actions → driver-android → Run workflow**, or push a change under `apps/driver-pwa` or
   `apps/driver-android`.
3. Open the finished run and download the artifact `agripulse-driver-debug` (it is a zip containing
   `app-debug.apk`).
   - If you see `agripulse-driver-debug-NO-API`, the variable wasn't set. That APK installs but can't sign in.
4. Make sure the server's `CORS_ORIGINS` includes `https://localhost`, which is the app's origin. It is in the
   defaults and in `.env.example`.

**Build locally instead** (needs Android Studio or the Android SDK, and JDK 21):

```
cd apps/driver-android
npm ci
AGRIPULSE_API=https://<domain>/api npm run apk:debug   # -> android/app/build/outputs/apk/debug/app-debug.apk
```

## Install on the driver's phone

1. Copy the APK to the phone (WhatsApp to self, USB or Drive) and open it. Allow "install unknown apps" for
   whichever app opened it.
2. Open **AgriPulse Driver** and sign in with the driver account.
3. When starting a trip, allow **location "While using the app"** and **notifications**.
4. **Battery settings, required on most phones sold in India.** Go to Settings → Apps → AgriPulse Driver →
   Battery → **Unrestricted** (or "Don't optimise"). The app's checklist has an "Open app settings" button.
   - **Xiaomi / Redmi / POCO:** also Autostart → on, and Battery saver → No restrictions.
   - **Oppo / Realme / OnePlus (ColorOS):** Battery → allow background activity, and turn on Auto launch.
   - **Vivo (Funtouch / OriginOS):** Battery → High background power consumption → allow.
   - **Samsung:** Battery → Unrestricted, and remove the app from "Sleeping apps".
   - Without this, some phones kill the service after a few minutes with the screen off. See
     https://dontkillmyapp.com for your model.

## How it differs from the browser app

| | Browser (PWA) | Android app |
|---|---|---|
| Screen locked, on a call, Maps in front | no location (browser limit); the server shows "location paused" | location continues |
| Screen lock / wake lock | held while tracking, re-requested after every interruption | not needed |
| "Tracking on" indicator | red bar in the app | red bar in the app plus an ongoing notification |
| Uploads | WebSocket, or HTTP when the socket is down | WebSocket while on screen; native HTTP in the background (Android throttles the web view after about 5 min) |
| Offline buffer | IndexedDB, flushed when back online | same |

## Not done yet

- **iOS.** Needs a Mac, Xcode and an Apple Developer account ($99/year). The plugin supports it: add
  `Info.plist` keys and the `location` background mode.
- **Play Store.** Needs a signed release build and the Play Console foreground-service (location) declaration.
  Signing keys must never be committed; use CI secrets.
- **A one-tap "ignore battery optimisation" prompt.** Drivers get written steps instead.
- The plugin declares its location service `exported="true"` in its own manifest. That is upstream behaviour;
  it is not changed here.
- The app icon is Capacitor's default.
