"""B-2 investigation: run the REAL apps/driver-pwa/app.js in Chromium and simulate a screen lock.
Screen lock on Android Chrome / iOS Safari = page hidden, then frozen: no JS runs, no geolocation callbacks.
We emulate: (1) visibilitychange -> hidden, (2) spec Wake Lock behaviour (sentinel auto-released on hide),
(3) CDP Page.setWebLifecycleState frozen. Fixes are emitted by a mocked watchPosition every TICK ms (= 5 s of trip)."""
import asyncio, functools, http.server, json, threading
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2] / "apps" / "driver-pwa"
TICK = 100  # ms per simulated 5-second GPS fix

INIT = """
(() => {
  window.AGRIPULSE_API = location.origin + '/api';
  localStorage.setItem('ap_driver_token', 'x');
  window.__fixes = 0; window.__wlRequests = 0; window.__hidden = false;
  // fetch: empty trip list, anything else ok
  window.fetch = async (u, o) => new Response(JSON.stringify(String(u).includes('/trips?') ? [] : {}), {status: 200});
  window.WebSocket = class { constructor(){ this.readyState = 0; } send(){} close(){} };
  // spec Wake Lock: released automatically when the document is hidden
  const sentinels = [];
  Object.defineProperty(navigator, 'wakeLock', {value: {request: async (t) => {
    if (window.__hidden) throw new DOMException('hidden', 'NotAllowedError');
    window.__wlRequests++;
    const s = new EventTarget(); s.released = false; s.type = t;
    s.release = async () => { if (!s.released) { s.released = true; s.dispatchEvent(new Event('release')); } };
    sentinels.push(s); return s; }}});
  window.__lock = () => {
    window.__hidden = true;
    Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
    Object.defineProperty(document, 'hidden', {value: true, configurable: true});
    sentinels.forEach(s => s.release());
    document.dispatchEvent(new Event('visibilitychange'));
  };
  window.__unlock = () => {
    window.__hidden = false;
    Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
    Object.defineProperty(document, 'hidden', {value: false, configurable: true});
    document.dispatchEvent(new Event('visibilitychange'));
  };
  window.__wlHeld = () => sentinels.some(s => !s.released);
  let t0 = Date.now();
  Object.defineProperty(navigator, 'geolocation', {value: {
    watchPosition: (ok) => setInterval(() => { window.__fixes++;
      ok({timestamp: Date.now(), coords: {latitude: 13.1 + window.__fixes * 1e-4, longitude: 78.1, speed: 11, accuracy: 10}}); }, %d),
    clearWatch: (id) => clearInterval(id)}});
})();
""" % TICK


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
        def translate_path(self, path):
            p = path.split("?")[0].replace("/api/driver/", "/").replace("/driver/", "/")
            return str(ROOT / p.lstrip("/")) if p != "/" else str(ROOT / "index.html")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def buffered(page):
    return await page.evaluate("""async () => (await new Promise(r => { const q = indexedDB.open('agripulse-driver', 1);
      q.onsuccess = () => { const g = q.result.transaction('points').objectStore('points').getAll(); g.onsuccess = () => r(g.result); }; })).length""")


async def main():
    srv = serve()
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        page = await ctx.new_page()
        await page.add_init_script(INIT)
        # never flush: we count what the phone RECORDED, not what reached the server
        await page.route("**/*", lambda r: r.continue_())
        await page.goto("http://127.0.0.1:8765/api/driver/")
        await page.evaluate("navigator.__defineGetter__('onLine', () => false)")
        cdp = await ctx.new_cdp_session(page)
        await page.evaluate("startTracking(1)")
        await asyncio.sleep(0.05)
        out["wake_lock_after_start"] = await page.evaluate("window.__wlHeld()")

        async def phase(name, ticks, locked):
            f0, b0 = await page.evaluate("window.__fixes"), await buffered(page) if not locked else None
            if locked:
                await page.evaluate("window.__lock()")
                other = await ctx.new_page(); await other.bring_to_front()   # the real page is now hidden
                vis = await page.evaluate("document.visibilityState")
                await cdp.send("Page.setWebLifecycleState", {"state": "frozen"})
                await asyncio.sleep(ticks * TICK / 1000)
                await cdp.send("Page.setWebLifecycleState", {"state": "active"})
                await other.close(); await page.bring_to_front()
                await page.evaluate("window.__unlock()")
                out.setdefault("browser_visibility_while_locked", vis)
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(ticks * TICK / 1000)
            out[name] = {"expected_fixes": ticks, "fixes_delivered": await page.evaluate("window.__fixes") - f0,
                         "wake_lock_held_after": await page.evaluate("window.__wlHeld()"),
                         "wake_lock_requests_total": await page.evaluate("window.__wlRequests")}

        await phase("1_screen_on_2.5min", 30, False)
        await phase("2_locked_10min", 120, True)
        await phase("3_after_unlock_2.5min", 30, False)
        await phase("4_locked_again_10min", 120, True)
        out["buffered_total"] = await buffered(page)
        out["expected_total_if_never_locked"] = 30 + 120 + 30 + 120
        await b.close()
    srv.shutdown()
    print(json.dumps(out, indent=1))

asyncio.run(main())
