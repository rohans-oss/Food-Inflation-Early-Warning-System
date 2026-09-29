"""Browser run-through: Tejas's journey via the real web UI and the driver PWA, then every role page.

Not part of  (needs running servers). Against a FRESH demo database:
    python -m agripulse_api.seed --demo && python -m ingest.run synthetic
    python -m agripulse_ml.train --synthetic --folds 3 && python -m agripulse_ml.predict
    uvicorn agripulse_api.main:app --app-dir services/api --port 8000 &
    (cd apps/web && npm run build && npm start) &
    pip install playwright && playwright install chromium
    python tests/e2e/journey_ui.py            # screenshots -> tests/e2e/shots/
The driver's phone is emulated: Playwright feeds GPS positions from the farm to Kolar APMC
into the PWA's own geolocation watch, so the real ingest path is exercised.
"""
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

WEB = os.environ.get("WEB_URL", "http://localhost:3000")
API = os.environ.get("API_URL", "http://localhost:8000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(OUT, exist_ok=True)
PW = "agripulse-demo"
FARM = (13.20, 78.02)
KOLAR = (13.137, 78.129)
errors: list[str] = []


def api(path, token=None, body=None, method=None):
    req = urllib.request.Request(API + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def tok(email, pw=PW):
    return api("/auth/login", body={"email": email, "password": pw})["access_token"]


def watch(page, name):
    def on_console(m):
        if m.type == "error" and "tile.openstreetmap.org" not in m.text and "Failed to load resource" not in m.text:
            errors.append(f"[{name}] console: {m.text[:300]}")
    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"[{name}] pageerror: {e}"))
    # no internet in the sandbox: stub map tiles so MapLibre doesn't spam errors
    page.route("https://tile.openstreetmap.org/**", lambda r: r.fulfill(status=204, body=b""))


def login(page, email, pw=PW):
    page.goto(WEB + "/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(pw)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)


def shot(page, name):
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)


def step(msg):
    print("•", msg, flush=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    mk = lambda: b.new_context(viewport={"width": 1280, "height": 900}, permissions=["geolocation"], geolocation={"latitude": FARM[0], "longitude": FARM[1]})

    # 1. farmer registers a lot
    fctx = mk(); fp = fctx.new_page(); watch(fp, "farmer")
    login(fp, "tejas@demo.agripulse")
    expect(fp.get_by_role("heading", name="My lots")).to_be_visible()
    fp.get_by_role("button", name="Use my location").click()
    fp.get_by_text("Pickup point: 13.2").wait_for()
    fp.get_by_label("Quantity (tonnes)").fill("2")
    fp.get_by_label("Pickup place name").fill("Tejas farm, Vemagal")
    fp.get_by_label("FPO").select_option(label="Kolar Tomato Growers FPO (demo)")
    fp.get_by_label("Lender (optional)").select_option(label="Grama Credit Co-op (demo)")
    fp.get_by_role("button", name="Register lot").click()
    fp.wait_for_url("**/farmer/lots/*")
    lot_id = int(fp.url.rsplit("/", 1)[1])
    fp.get_by_text("Best mandi for this lot").wait_for()
    fp.locator("table").first.get_by_text("Kolar APMC").wait_for(timeout=15000)
    shot(fp, "01-farmer-lot-best-mandi")
    step(f"farmer registered lot #{lot_id}, recommender shown")
    fp.goto(WEB + "/farmer"); fp.get_by_text("Today's prices near you").wait_for(); fp.get_by_text("Model p50").wait_for(timeout=15000)
    shot(fp, "02-farmer-home")

    # 2. FPO groups + books
    fpo = mk().new_page(); watch(fpo, "fpo")
    login(fpo, "fpo@demo.agripulse")
    fpo.get_by_label(f"Select lot {lot_id}", exact=True).check()
    fpo.get_by_label("Mandi", exact=True).select_option(label="Kolar APMC")
    fpo.get_by_role("button", name="Group into a shipment").click()
    fpo.get_by_text("Shipment #").first.wait_for()
    fpo.get_by_label("Book a fleet").first.select_option(label="Hebbal Haulage (demo)")
    fpo.get_by_text("Fleet: Hebbal Haulage (demo)").first.wait_for()
    shot(fpo, "03-fpo")
    step("FPO grouped the lot and booked a fleet")

    # 3. fleet owner assigns
    fl = mk().new_page(); watch(fl, "fleet")
    login(fl, "fleet@demo.agripulse")
    fl.get_by_label("Vehicle", exact=True).first.select_option(label="KA-01-XX-1234 (5 t)")
    fl.get_by_label("Driver", exact=True).first.select_option(label="Ravi (driver)")
    fl.get_by_role("button", name="Assign").first.click()
    fl.get_by_text("No open bookings.").wait_for()
    step("fleet owner assigned vehicle + driver")

    # 4. driver PWA: accept, consent, start, GPS from the (emulated) phone
    dctx = mk(); dp = dctx.new_page(); watch(dp, "driver-pwa")
    dp.goto(API + "/driver/")
    dp.get_by_label("Email").fill("driver@demo.agripulse"); dp.get_by_label("Password").fill(PW)
    dp.get_by_role("button", name="Sign in").click()
    dp.get_by_text("KA-01-XX-1234").first.click()
    dp.get_by_role("button", name="Accept trip").click()
    dp.get_by_label("I agree to share my location for this trip only").check()
    dp.wait_for_timeout(500)
    dp.get_by_role("button", name="Start trip").click()
    dp.get_by_text("TRACKING ON").wait_for()
    shot(dp, "04-driver-pwa-tracking-on")
    step("driver accepted, consented, started; TRACKING ON visible")

    # pickup QR: farmer's screen shows the token; driver submits it (manual entry = same endpoint as camera)
    ftok = tok("tejas@demo.agripulse")
    token = api(f"/lots/{lot_id}", ftok)["trip"]["pickup_qr_token"]
    dp.get_by_placeholder("…or type the code").fill(token)
    dp.get_by_role("button", name="Submit").click()
    dp.get_by_text("Pickup confirmed").wait_for()
    step("pickup QR accepted")

    # drive: farm -> Kolar APMC via the PWA's own geolocation stream
    n = 12
    for i in range(1, n + 1):
        lat = FARM[0] + (KOLAR[0] - FARM[0]) * i / n
        lon = FARM[1] + (KOLAR[1] - FARM[1]) * i / n
        dctx.set_geolocation({"latitude": lat, "longitude": lon})
        dp.wait_for_timeout(700)
        if i == 6:
            fp.goto(WEB + f"/farmer/lots/{lot_id}")
            fp.get_by_text("Live vehicle").wait_for()
            fp.get_by_text("km away, arriving").wait_for(timeout=15000)
            shot(fp, "05-farmer-live-vehicle")
            step("farmer sees live vehicle with ETA mid-route")
    dp.wait_for_timeout(1500)
    trip = api(f"/lots/{lot_id}", ftok)["trip"]
    ev = [e["event"] for e in api(f"/trips/{trip['id']}", ftok)["events"]]
    assert "reached_mandi" in ev and "left_pickup_zone" in ev, ev
    step(f"geofence events: {ev}")

    # 5. trader: scan driver's delivery QR, weigh
    tr = mk().new_page(); watch(tr, "trader")
    login(tr, "trader@demo.agripulse")
    tr.get_by_text("KA-01-XX-1234").first.wait_for()
    shot(tr, "06-trader-board")
    dtoken = api(f"/trips/{trip['id']}", tok("driver@demo.agripulse"))["delivery_qr_token"]
    tr.get_by_role("button", name="Scan delivery QR").click()
    tr.get_by_placeholder("…or paste the code").fill(dtoken)
    tr.get_by_role("button", name="Submit").click()
    tr.get_by_text("Arrival confirmed").wait_for()
    tr.get_by_label("Weight in kg").fill("1985")
    tr.get_by_label("Price per quintal").fill("1400")
    tr.get_by_role("button", name="Record weight and price").click()
    tr.get_by_text(f"Lot #{lot_id} recorded").wait_for()
    step("trader confirmed arrival by QR and weighed the lot")

    # "End trip" in the PWA opens a confirm() dialog; end through the same endpoint directly
    api(f"/trips/{trip['id']}/end", tok("driver@demo.agripulse"), body={})

    # 6. farmer delivery confirmation, FPO payout, lender verification
    fp.goto(WEB + f"/farmer/lots/{lot_id}")
    fp.get_by_text("1,985 kg").wait_for(timeout=15000)
    shot(fp, "07-farmer-delivered")
    step("farmer sees delivery confirmation")
    fpo.reload(); fpo.get_by_role("button", name="Details").first.click()
    fpo.get_by_role("button", name="Mark paid").click()
    fpo.wait_for_timeout(800)
    shot(fpo, "08-fpo-paid")
    le = mk().new_page(); watch(le, "lender")
    login(le, "lender@demo.agripulse")
    le.get_by_text(f"#{lot_id}").first.wait_for()
    shot(le, "09-lender")
    step("FPO marked paid; lender sees the verified chain")

    # 7. remaining roles render
    for email, name, pw in [("buyer@demo.agripulse", "10-buyer", PW), ("policy@demo.agripulse", "11-policy", PW),
                            ("admin@agripulse.local", "12-admin", "agripulse-admin")]:
        pg = mk().new_page(); watch(pg, name)
        login(pg, email, pw)
        pg.wait_for_load_state("networkidle")
        shot(pg, name)
    dv = mk().new_page(); watch(dv, "driver-web"); login(dv, "driver@demo.agripulse"); shot(dv, "13-driver-web")
    share = api(f"/trips/{trip['id']}", ftok).get("share_url")
    step(f"all role pages rendered; share url {share}")

    # mobile width check on the farmer page
    m = b.new_context(viewport={"width": 390, "height": 844}).new_page(); watch(m, "mobile")
    login(m, "tejas@demo.agripulse")
    wide = m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    shot(m, "14-farmer-mobile")
    step(f"mobile horizontal overflow: {wide}")
    b.close()

print("\nERRORS:" if errors else "\nno console errors")
for e in errors:
    print(" ", e)
sys.exit(1 if errors else 0)
