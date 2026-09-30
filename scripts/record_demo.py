"""Record the two demo videos from a running DEMO stack (V3-4). Nothing here is real-world footage:

  1. tejas.mp4      ~3 min  Tejas's journey, lot -> delivery. The truck is SIMULATED: the script marks the demo trip
                            is_simulated = true BEFORE posting any position, so every screen shows the Simulated badge.
                            The real-phone video (docs/field-test.md) is still yours to record.
  2. decisions.mp4  ~1.5 min optimizer (best mandi, rule vs optimizer), shared loads, scenario simulator.

Prices and forecasts in the demo database are SYNTHETIC and every screen says so; a caption bar repeats it.

Prerequisites (README "Quick start" demo path): API on --api and web on --web, both on the SAME database as
DATABASE_URL here (`seed --demo`, `ingest.run synthetic`, `train --synthetic`, `predict`), a fresh copy with no lots.

    DATABASE_URL=sqlite:///demo.db python scripts/record_demo.py --out demo-videos
Needs playwright (Chromium) and ffmpeg.
"""
import argparse
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

DEMO_PW, ADMIN = "agripulse-demo", ("admin@agripulse.local", "agripulse-admin")
USERS = {r: f"{e}@demo.agripulse" for r, e in [("farmer", "tejas"), ("fpo", "fpo"), ("driver", "driver"),
                                               ("fleet_owner", "fleet"), ("trader", "trader"), ("lender", "lender"),
                                               ("policy", "policy")]}
FARM = (13.20, 78.02)
BANNER = "DEMO RECORDING · prices & forecasts SYNTHETIC · truck SIMULATED"


class Demo:
    def __init__(self, api: str, web: str, out: Path):
        self.api, self.web, self.out = api.rstrip("/"), web.rstrip("/"), out
        self.http = httpx.Client(base_url=self.api, timeout=60)
        self.sessions = {}
        for role, email in USERS.items():
            self.sessions[role] = self._login(email, DEMO_PW)
        self.sessions["admin"] = self._login(*ADMIN)

    def _login(self, email, pw):
        r = self.http.post("/auth/login", json={"email": email, "password": pw})
        r.raise_for_status()
        return r.json()

    def call(self, role, method, path, **kw):
        h = {"Authorization": f"Bearer {self.sessions[role]['access_token']}"}
        r = self.http.request(method, path, headers=h, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    # ---- recording helpers -----------------------------------------------------------------------------------
    def scene(self, browser, role, url, captions, *, mobile=False, act=None, clips=None, init=None, **ctx_kw):
        """One clip: a fresh context signed in as `role`, recorded; captions = [(seconds, text), ...].
        `act` maps a caption to a function run on the page right after that caption appears."""
        size = {"width": 390, "height": 844} if mobile else {"width": 1280, "height": 720}
        tmp = self.out / "_raw"
        ctx = browser.new_context(viewport=size, record_video_dir=str(tmp), record_video_size=size,
                                  device_scale_factor=1, **ctx_kw)
        if init:
            ctx.add_init_script(init)
        elif role:
            ctx.add_init_script(f"localStorage.setItem('agripulse_session', {json.dumps(json.dumps(self.sessions[role]))})")
        page = ctx.new_page()
        page.goto(url if url.startswith("http") else self.web + url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)
        for secs, text in captions:
            self._caption(page, text)
            if act and text in act:
                act[text](page)
            page.wait_for_timeout(int(secs * 1000))
        path = page.video.path()
        ctx.close()
        clips.append(Path(path))

    @staticmethod
    def _caption(page, text):
        page.evaluate("""([t, b]) => {
          let el = document.getElementById('demo-cap');
          if (!el) { el = document.createElement('div'); el.id = 'demo-cap';
            el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;background:rgba(17,17,17,.88);color:#fff;'
              + 'font:600 17px/1.35 system-ui,sans-serif;padding:10px 16px;pointer-events:none';
            document.body.appendChild(el); }
          el.innerHTML = '<div style="font:500 11px system-ui;color:#fca5a5;letter-spacing:.04em">' + b + '</div>' + t;
        }""", [text, BANNER])

    def stitch(self, clips, name):
        lst = self.out / f"{name}.txt"
        lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
        target = self.out / f"{name}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=white,fps=25",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", str(target)]
        subprocess.run(cmd, check=True)
        lst.unlink()
        return target

    # ---- data set-up -----------------------------------------------------------------------------------------
    def org(self, kind, contains):
        return next(o["id"] for o in self.call("farmer", "GET", "/orgs/directory", params={"kind": kind}) if contains in o["name"])

    def make_lot(self, lat=FARM[0], lon=FARM[1], qty=2, label="Tejas farm, Vemagal"):
        return self.call("farmer", "POST", "/lots", json={
            "quantity_tons": qty, "grade": "Local", "pickup_label": label, "pickup_lat": lat, "pickup_lon": lon,
            "fpo_org_id": self.org("fpo", "FPO"), "lender_org_id": self.org("lender", "")})


def mark_simulated(trip_id: int):
    """Rule 1: the demo truck is not a real phone, so the trip is simulated before any position exists."""
    from agripulse_api.db import SessionLocal
    from agripulse_api.models import Trip

    with SessionLocal() as db:
        t = db.get(Trip, trip_id)
        t.is_simulated = True
        db.commit()


def tejas(d: Demo, browser) -> Path:
    clips = []
    lot = d.make_lot()
    mandis = d.call("farmer", "GET", "/recommend/best-mandi", params={"lot_id": lot["id"]})
    kolar = next(m for m in mandis["ranked"] if "Kolar" in m["mandi"])

    d.scene(browser, "policy", "/policy", [
        (6, "Tomato prices in India can swing 3–5× in weeks. AgriPulse forecasts them as ranges, not single numbers…"),
        (6, "…and watches supply physically moving toward mandis. Every number here is labelled: this demo runs on SYNTHETIC prices.")],
        clips=clips)
    d.scene(browser, "farmer", f"/farmer/lots/{lot['id']}", [
        (6, "Tejas, a farmer near Kolar, registers 2 tonnes and drops a pin at his farm."),
        (8, "Best mandi: net value after transport and spoilage, as a p10–p90 range. The OR-Tools optimizer ranks them.")],
        act={"Best mandi: net value after transport and spoilage, as a p10–p90 range. The OR-Tools optimizer ranks them.":
             lambda p: p.mouse.wheel(0, 500)}, clips=clips)

    sh = d.call("fpo", "POST", "/shipments", json={"mandi_id": kolar["mandi_id"], "lot_ids": [lot["id"]]})
    d.call("fpo", "POST", f"/shipments/{sh['id']}/book", json={"fleet_org_id": d.org("fleet", "Haulage")})
    d.scene(browser, "fpo", "/fpo", [(6, "His FPO groups the lot into a shipment to Kolar APMC and books a fleet.")], clips=clips)

    d.scene(browser, "fleet_owner", "/fleet", [(6, "The booking reaches a fleet owner, who assigns a truck and a driver.")],
            clips=clips)
    veh = next(v for v in d.call("fleet_owner", "GET", "/vehicles") if v["registration"] == "KA-01-XX-1234")
    me = d.sessions["driver"]["user"]["id"]
    drv = next(x for x in d.call("fleet_owner", "GET", "/drivers") if x["id"] == me)
    trip = d.call("fleet_owner", "POST", "/trips", json={"shipment_id": sh["id"], "vehicle_id": veh["id"], "driver_id": drv["id"]})
    mark_simulated(trip["id"])  # before any position exists

    # the real driver app (apps/driver-pwa, served by the API at /driver/), clicked through like a phone
    drv_s = d.sessions["driver"]
    init = (f"localStorage.setItem('ap_driver_token', {json.dumps(drv_s['access_token'])});"
            f"localStorage.setItem('ap_driver_refresh', {json.dumps(drv_s['refresh_token'])});")
    c_open, c_consent, c_scan = ("Driver app (this recording's truck is SIMULATED): the trip appears; the driver accepts it.",
                                 "Tracking needs explicit consent, only for this trip. Then: TRACKING ON, visible the whole time.",
                                 "At the farm the driver scans Tejas's QR (typed here): the lot is now in transit.")

    def open_accept(p):
        p.locator("#trips button").first.click()
        p.wait_for_timeout(1500)
        p.get_by_role("button", name="Accept trip").click()

    def consent_start(p):
        p.locator("#consent").check()
        p.wait_for_timeout(1500)
        p.get_by_role("button", name="Start trip").click()

    def scan(p):
        tok = d.call("farmer", "GET", f"/lots/{lot['id']}")["trip"]["pickup_qr_token"]  # on Tejas's screen once started
        p.locator("#scanBox").scroll_into_view_if_needed()
        p.locator("#manualToken").fill(tok)
        p.wait_for_timeout(800)
        p.locator("#manualBtn").click()

    d.scene(browser, None, d.api + "/driver/", [(5, c_open), (6, c_consent), (6, c_scan)], mobile=True, init=init,
            act={c_open: open_accept, c_consent: consent_start, c_scan: scan}, clips=clips,
            geolocation={"latitude": FARM[0], "longitude": FARM[1]}, permissions=["geolocation"])
    assert d.call("farmer", "GET", f"/lots/{lot['id']}")["status"] == "in_transit", "driver-app pickup scan did not register"

    # drive: positions through the same endpoint a phone uses, paced so the map moves on camera
    stop = threading.Event()
    k = _mandi_point(d, kolar["mandi_id"])
    route = [(FARM[0] + (k[0] - FARM[0]) * i / 60, FARM[1] + (k[1] - FARM[1]) * i / 60) for i in range(61)]

    def drive(until: int):
        while drive.i < until and not stop.is_set():
            la, lo = route[drive.i]
            d.call("driver", "POST", f"/trips/{trip['id']}/points", json={"points": [
                {"recorded_at": datetime.now(timezone.utc).isoformat(), "lat": la, "lon": lo, "speed_kmph": 42}]})
            drive.i += 1
            time.sleep(0.45)
    drive.i = 0
    drive(20)
    th = threading.Thread(target=drive, args=(55,))
    th.start()
    d.scene(browser, "farmer", f"/farmer/lots/{lot['id']}", [
        (8, "Tejas watches the truck live: distance left and ETA, updating as positions arrive."),
        (8, "He can share a public link: expiring, unguessable, and it shows only position, ETA and lot status.")],
        act={"Tejas watches the truck live: distance left and ETA, updating as positions arrive.": lambda p: p.mouse.wheel(0, 350)},
        clips=clips)
    th.join()
    share = d.call("farmer", "GET", f"/trips/{trip['id']}")["share_url"]
    d.scene(browser, None, "/track/" + share.rsplit("/", 1)[1], [(6, "The public tracking page, opened with no login.")], clips=clips)

    d.scene(browser, "trader", "/trader", [
        (8, "The trader at Kolar sees the incoming truck: supply that official data will only report tomorrow.")], clips=clips)
    drive(61)
    dtok = d.call("driver", "GET", f"/trips/{trip['id']}")["delivery_qr_token"]
    d.call("trader", "POST", f"/trips/{trip['id']}/scan/delivery", json={"token": dtok})
    d.call("trader", "POST", f"/trader/lots/{lot['id']}/weigh", json={"weight_kg": 1985, "price_per_quintal": 1400})
    d.call("driver", "POST", f"/trips/{trip['id']}/end")
    d.scene(browser, "farmer", f"/farmer/lots/{lot['id']}", [
        (7, "Delivery scanned and weighed: 1,985 kg. Tejas gets the confirmation.")], clips=clips)
    d.scene(browser, "lender", "/lender", [(6, "His lender sees the verified chain: pickup QR, tracked trip, delivery QR, weight.")],
            clips=clips)
    d.scene(browser, "admin", "/admin", [
        (7, "Admin: data freshness and a walk-forward backtest against a naive baseline."),
        (8, "Honest result: on SYNTHETIC data no model beats 'today's price' yet. Real prices started 2026-09-25.")],
        act={"Honest result: on SYNTHETIC data no model beats 'today's price' yet. Real prices started 2026-09-25.":
             lambda p: p.mouse.wheel(0, 900)}, clips=clips)
    return d.stitch(clips, "tejas")


def _mandi_point(d: Demo, mandi_id: int):
    m = next(x for x in d.call("admin", "GET", "/admin/mandis/locations") if x["id"] == mandi_id)
    return m["lat"], m["lon"]


def decisions(d: Demo, browser) -> Path:
    clips = []
    for i, (la, lo) in enumerate([(13.18, 78.05), (13.22, 78.00), (13.15, 78.10), (13.25, 78.07)]):
        d.make_lot(la, lo, qty=1 + (i % 2), label=f"Neighbour farm {i + 1}")
    lot = d.make_lot(13.21, 78.03, qty=3, label="Tejas farm, second picking")

    d.scene(browser, "farmer", f"/farmer/lots/{lot['id']}", [
        (7, "Best mandi, now from an OR-Tools CP-SAT optimizer: net value = price − transport − spoilage, with ranges."),
        (7, "Options that break a hard limit (spoilage cap, mandi room) are listed last with the reason.")],
        act={"Best mandi, now from an OR-Tools CP-SAT optimizer: net value = price − transport − spoilage, with ranges.":
             lambda p: p.mouse.wheel(0, 500)}, clips=clips)

    def compare(p):
        p.get_by_role("button", name="Compare").first.scroll_into_view_if_needed()
        p.get_by_role("button", name="Compare").first.click()
        p.wait_for_timeout(9000)
    d.scene(browser, "admin", "/admin", [
        (4, "Rule vs optimizer on the SAME simulated batch, scored by one evaluator…"),
        (8, "On this one batch the rule earns more only by overloading a mandi (see Violations)…"),
        (8, "…across 240 simulated days: net value a tie (+0.2%), overload violations 140 → 0, transport −22 to −34%. SYNTHETIC.")],
        act={"Rule vs optimizer on the SAME simulated batch, scored by one evaluator…": compare}, clips=clips)

    def plan(p):
        p.get_by_role("button", name="Plan loads").click()
        p.wait_for_timeout(6000)
        p.get_by_role("button", name="Plan loads").scroll_into_view_if_needed()
    d.scene(browser, "fpo", "/fpo", [
        (3, "Shared truckloads: the FPO asks for a plan across its pending lots…"),
        (8, "…and gets proposed shared loads with estimated savings to accept or reject. Nothing moves until a person accepts.")],
        act={"Shared truckloads: the FPO asks for a plan across its pending lots…": plan}, clips=clips)

    def scenario(p):
        p.get_by_role("button", name="Run scenario").scroll_into_view_if_needed()
        p.get_by_role("button", name="Run scenario").click()
        p.wait_for_timeout(8000)
        p.get_by_text("COUNTERFACTUAL ESTIMATE").first.scroll_into_view_if_needed()
    d.scene(browser, "policy", "/policy", [
        (3, "Scenario simulator: a 50% rain deficit two to four months ago in the tomato belt…"),
        (9, "Two answers, never blended: a sourced assumption chain (about +10%, range +0.6 to +49%) and what the model does."),
        (7, "Every output says COUNTERFACTUAL ESTIMATE — not a validated causal model.")],
        act={"Scenario simulator: a 50% rain deficit two to four months ago in the tomato belt…": scenario}, clips=clips)
    return d.stitch(clips, "decisions")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--web", default="http://localhost:3000")
    ap.add_argument("--out", default="demo-videos")
    ap.add_argument("--only", choices=["tejas", "decisions"])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = Demo(a.api, a.web, out)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        made = []
        if a.only in (None, "tejas"):
            made.append(tejas(d, b))
        if a.only in (None, "decisions"):
            made.append(decisions(d, b))
        b.close()
    shutil.rmtree(out / "_raw", ignore_errors=True)
    for m in made:
        print(m)


if __name__ == "__main__":
    main()
