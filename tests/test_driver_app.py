"""Pre-V3 B-2: the REAL apps/driver-pwa files in headless Chromium, as the browser app and as the Android app
(Capacitor bridge + background-geolocation plugin mocked). The Android APK itself is built in CI
(.github/workflows/driver-android.yml); whether GPS survives a locked screen on a real phone is the field test's job.
Skips when Playwright / Chromium are not installed (pip install -e .[browser])."""
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PWA = Path(__file__).resolve().parents[1] / "apps" / "driver-pwa"
TRIP = {"id": 7, "vehicle": "KA-01-XX-1234", "mandi": "Kolar APMC", "status": "in_progress", "is_simulated": False,
        "load_tons": 2, "planned_distance_km": 20, "consent_given_at": "2026-10-01T04:00:00Z", "shipment_id": 1,
        "pickup_scanned_at": "2026-10-01T04:05:00Z", "delivery_qr_token": None, "remaining_km": 12.0,
        "eta_local": "11:00 AM IST", "last_seen_at": None, "route_source": "osrm"}

BASE_INIT = """
window.AGRIPULSE_API = location.origin + '/api';
localStorage.setItem('ap_driver_token', 'tok');
window.WebSocket = class { constructor(){ this.readyState = 0; } send(){} close(){} };
window.__vis = 'visible';
Object.defineProperty(document, 'visibilityState', {get: () => window.__vis, configurable: true});
window.__setVisible = (v) => { window.__vis = v ? 'visible' : 'hidden';
  if (!v) window.__sentinels.forEach(s => s.release());   // the browser drops the screen lock on hide (spec)
  document.dispatchEvent(new Event('visibilitychange')); };
window.__sentinels = []; window.__wlRequests = 0;
Object.defineProperty(navigator, 'wakeLock', {value: {request: async (type) => {
  if (window.__vis !== 'visible') throw new DOMException('hidden', 'NotAllowedError');
  window.__wlRequests++;
  const s = new EventTarget(); s.released = false;
  s.release = async () => { if (!s.released) { s.released = true; s.dispatchEvent(new Event('release')); } };
  window.__sentinels.push(s); return s; }}});
window.__wlHeld = () => window.__sentinels.some(s => !s.released);
window.__emit = null;
Object.defineProperty(navigator, 'geolocation', {value: {
  watchPosition: (ok) => { window.__emit = (t, lat) => ok({timestamp: t, coords: {latitude: lat, longitude: 78.1, speed: 10, accuracy: 8}}); return 1; },
  clearWatch: () => { window.__emit = null; }}});
"""

NATIVE_INIT = """
window.__native = {watchers: {}, removed: [], notif: 0, settings: 0, next: 0};
window.Capacitor = {
  isNativePlatform: () => true,
  registerPlugin: (name) => ({
    BackgroundGeolocation: {
      addWatcher: async (opts, cb) => { const id = 'w' + (++window.__native.next); window.__native.watchers[id] = {opts, cb}; return id; },
      removeWatcher: async ({id}) => { window.__native.removed.push(id); delete window.__native.watchers[id]; },
      openSettings: async () => { window.__native.settings++; },
    },
    LocalNotifications: { requestPermissions: async () => { window.__native.notif++; return {display: 'granted'}; } },
  })[name],
};
window.__nativeFix = (t, lat) => Object.values(window.__native.watchers).forEach(w => w.cb({latitude: lat, longitude: 78.1,
  accuracy: 6, speed: 10, time: t}));
"""


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                                directory=str(PWA))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # no Chromium on this machine
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def open_app(browser, server, native=False, points_status=200):
    ctx = browser.new_context()
    page = ctx.new_page()
    calls = []

    def api(route):
        req = route.request
        path = req.url.split("/api", 1)[1]
        calls.append({"method": req.method, "path": path, "body": req.post_data, "auth": req.headers.get("authorization")})
        if path.startswith("/trips?"):
            return route.fulfill(json=[TRIP])
        if path.endswith("/points"):
            if points_status != 200:
                return route.fulfill(status=points_status, json={"detail": "Tracking is only accepted during an active trip"})
            return route.fulfill(json={"accepted": len(json.loads(req.post_data)["points"]), "trip": {}})
        return route.fulfill(json={"paused": True} if path.endswith("/pause") else TRIP)

    page.route("**/api/**", api)
    page.add_init_script(BASE_INIT + (NATIVE_INIT if native else ""))
    page.goto(server + "/index.html")
    page.wait_for_function("document.getElementById('trackingBar').hidden === false")  # live trip -> tracking resumes
    return page, calls


def queued(page):
    return page.evaluate("""async () => (await new Promise(r => { const q = indexedDB.open('agripulse-driver', 1);
      q.onsuccess = () => { const g = q.result.transaction('points').objectStore('points').getAll(); g.onsuccess = () => r(g.result); }; }))""")


def test_browser_re_acquires_the_screen_lock_after_every_interruption(browser, server):
    page, _ = open_app(browser, server)
    page.wait_for_function("window.__wlHeld()")
    for _ in range(3):  # a call, a glance at Maps, the power button
        page.evaluate("window.__setVisible(false)")
        assert page.evaluate("window.__wlHeld()") is False
        page.evaluate("window.__setVisible(true)")
        page.wait_for_function("window.__wlHeld()")
    assert page.evaluate("window.__wlRequests") == 4  # B-2 finding: the old code stopped at 1


def test_browser_reports_the_pause_and_tells_the_driver_on_return(browser, server):
    page, calls = open_app(browser, server)
    page.evaluate("window.__setVisible(false)")
    page.wait_for_timeout(200)
    pauses = [c for c in calls if c["path"] == "/trips/7/pause"]
    assert len(pauses) == 1 and json.loads(pauses[0]["body"])["reason"] == "screen_off" and pauses[0]["auth"] == "Bearer tok"
    page.evaluate("window.__setVisible(false)")  # still hidden: no second report
    page.evaluate("hiddenAt = Date.now() - 12 * 60000")  # pretend the screen was off for 12 min
    page.evaluate("window.__setVisible(true)")
    note = page.locator("#notice")
    assert note.is_visible() and "paused for 12 min" in note.inner_text()
    assert len([c for c in calls if c["path"] == "/trips/7/pause"]) == 1
    page.click("button.trip")
    page.wait_for_selector("#checklist:not([hidden])")
    assert page.locator("#checkWeb").is_visible() and not page.locator("#checkNative").is_visible()


def test_browser_records_one_fix_per_5_seconds(browser, server):
    page, _ = open_app(browser, server)
    page.evaluate("navigator.__defineGetter__('onLine', () => false)")  # keep fixes in the buffer to count them
    page.evaluate("for (let i = 0; i < 30; i++) window.__emit(1790000000000 + i * 1000, 13.1 + i * 1e-4)")
    page.wait_for_timeout(300)
    assert len(queued(page)) == 6  # 30 s of 1-second fixes -> 6 kept


def test_android_app_uses_the_background_plugin_with_a_visible_notification(browser, server):
    page, calls = open_app(browser, server, native=True)
    page.wait_for_function("Object.keys(window.__native.watchers).length === 1")
    w = page.evaluate("Object.values(window.__native.watchers)[0].opts")
    assert "trip #7" in w["backgroundMessage"] and "tracking on" in w["backgroundTitle"]  # rule 4: visible indicator
    assert w["requestPermissions"] is True and page.evaluate("window.__native.notif") == 1
    assert page.evaluate("window.__wlRequests") == 0  # no screen lock needed: the phone can sleep
    page.evaluate("for (let i = 0; i < 12; i++) window.__nativeFix(1790000000000 + i * 1000, 13.1 + i * 1e-4)")
    page.wait_for_timeout(300)
    sent = [json.loads(c["body"])["points"] for c in calls if c["path"] == "/trips/7/points"]
    assert sum(len(p) for p in sent) == 3 and sent[0][0]["accuracy_m"] == 6  # 12 s of 1 Hz fixes -> 3, via HTTP
    page.evaluate("window.__setVisible(false)")  # screen locked: keeps recording, no pause report
    page.evaluate("window.__nativeFix(1790000020000, 13.2)")
    page.wait_for_timeout(200)
    assert not [c for c in calls if c["path"].endswith("/pause")]
    assert sum(len(json.loads(c["body"])["points"]) for c in calls if c["path"] == "/trips/7/points") == 4
    page.evaluate("window.__setVisible(true)")
    page.click("button.trip")  # the checklist is on the trip screen
    page.wait_for_selector("#checklist:not([hidden])")
    assert page.locator("#checkNative").is_visible() and not page.locator("#checkWeb").is_visible()
    page.locator("#openSettings").evaluate("b => b.click()")
    assert page.evaluate("window.__native.settings") == 1


def test_android_app_stops_gps_when_the_driver_logs_out(browser, server):
    page, _ = open_app(browser, server, native=True)
    page.wait_for_function("Object.keys(window.__native.watchers).length === 1")
    page.click("#logout")
    page.wait_for_function("window.__native.removed.length === 1")
    assert page.evaluate("Object.keys(window.__native.watchers).length") == 0
    assert page.locator("#trackingBar").is_hidden()


def test_android_app_stops_gps_when_the_server_refuses_points(browser, server):
    """Trip ended or consent withdrawn elsewhere: the phone must not keep tracking in the background (rule 4)."""
    page, _ = open_app(browser, server, native=True, points_status=409)
    page.wait_for_function("Object.keys(window.__native.watchers).length === 1")
    page.evaluate("window.__nativeFix(1790000000000, 13.1)")
    page.wait_for_function("window.__native.removed.length === 1")
    assert page.locator("#trackingBar").is_hidden() and len(queued(page)) == 0
    assert "stopped" in page.locator("#notice").inner_text()


def test_log_out_ends_the_session_on_the_server(browser, server):
    """Pre-V3 B-3: 'Log out' revokes this phone's session server-side, so a copied token stops working."""
    page, calls = open_app(browser, server)
    page.click("#logout")
    page.wait_for_timeout(200)
    out = [c for c in calls if c["path"] == "/auth/logout"]
    assert len(out) == 1 and out[0]["method"] == "POST" and out[0]["auth"] == "Bearer tok"
    assert page.evaluate("localStorage.getItem('ap_driver_token')") is None and page.locator("#loginView").is_visible()
    assert page.locator("#logoutAll").is_hidden()
