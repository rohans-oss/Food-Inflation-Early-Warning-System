/* AgriPulse driver app: the browser PWA AND the Android app (apps/driver-android wraps these same files).
 * - GPS only while a trip is in_progress AND the driver ticked consent (server enforces it too)
 * - every fix goes to IndexedDB first, then is flushed over WebSocket (or HTTP when the socket is down)
 * - re-sends are safe: the server de-duplicates on (trip, timestamp)
 * - Android app: location from the background-geolocation plugin (foreground service + "tracking on"
 *   notification), so it keeps working with the screen locked or Maps in front.
 * - Browser: location stops whenever the page is hidden (no web API can prevent it). The app keeps the screen on,
 *   tells the server "paused, screen off" when hidden, and tells the driver how long it was paused on return.
 */
// The PWA is served by the API at <api-root>/driver/, so the API root is the parent path:
// dev  http://localhost:8000/driver/     -> http://localhost:8000
// prod https://example.org/api/driver/   -> https://example.org/api   (behind Caddy)
const API = window.AGRIPULSE_API || new URL("..", location.href).href.replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
let token = localStorage.getItem("ap_driver_token");
let refreshToken = localStorage.getItem("ap_driver_refresh");
let current = null;      // trip being viewed
let tracking = null;     // id of the trip being tracked (null = not tracking)
let watchId = null;      // browser geolocation watch
let nativeWatcher = null; // Android background-geolocation watcher id
let ws = null;
let wakeLock = null;
let flushing = false;
let hiddenAt = null;     // browser: when the page was hidden during tracking
let lastFixMs = 0;
const MIN_FIX_MS = 5000; // one fix per 5 s (the native plugin reports every second)

// ------------------------------------------------------------ platform
const cap = window.Capacitor;
const NATIVE = !!(cap && cap.isNativePlatform && cap.isNativePlatform());
const plugin = (name) => (cap.registerPlugin ? cap.registerPlugin(name) : cap.Plugins[name]);
const BG = NATIVE ? plugin("BackgroundGeolocation") : null;

// ------------------------------------------------------------ IndexedDB buffer
const dbp = new Promise((res, rej) => {
  const r = indexedDB.open("agripulse-driver", 1);
  r.onupgradeneeded = () => r.result.createObjectStore("points", { keyPath: "k" });
  r.onsuccess = () => res(r.result);
  r.onerror = () => rej(r.error);
});
async function store(mode, fn) {
  const db = await dbp;
  return new Promise((res, rej) => {
    const tx = db.transaction("points", mode);
    const out = fn(tx.objectStore("points"));
    tx.oncomplete = () => res(out && out.result !== undefined ? out.result : out);
    tx.onerror = () => rej(tx.error);
  });
}
const bufAdd = (p) => store("readwrite", (s) => s.put(p));
const bufAll = () => store("readonly", (s) => s.getAll());
const bufDel = (keys) => store("readwrite", (s) => keys.forEach((k) => s.delete(k)));

// ------------------------------------------------------------ API
function saveTokens(r) {
  token = r.access_token; refreshToken = r.refresh_token;
  localStorage.setItem("ap_driver_token", token);
  localStorage.setItem("ap_driver_refresh", refreshToken);
}
// Access tokens are short-lived (30 min); a trip is longer, so refresh silently instead of logging out.
async function tryRefresh() {
  if (!refreshToken) return false;
  const r = await fetch(API + "/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }) }).catch(() => null);
  if (!r || !r.ok) return false;
  saveTokens(await r.json());
  return true;
}
async function api(path, opts = {}, retried = false) {
  const r = await fetch(API + path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(opts.headers || {}) },
  });
  if (r.status === 401 && !retried && !path.startsWith("/auth/") && await tryRefresh()) return api(path, opts, true);
  if (r.status === 401 && path.startsWith("/auth/")) {  // sign-in / link: say what the server said
    const b = await r.json().catch(() => ({}));
    throw new Error(b.detail || "Wrong email or password");
  }
  if (r.status === 401) { logout(); throw new Error("Session expired, please sign in again"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.detail || r.statusText); e.status = r.status; throw e; }
  return body;
}

// ------------------------------------------------------------ views
function show(view) {
  for (const v of ["loginView", "listView", "tripView"]) $(v).hidden = v !== view;
  $("logout").hidden = $("logoutAll").hidden = view === "loginView";
}
// B-3: "Log out" also ends this phone's session on the server (a copied token stops working); "All devices" ends
// every session of this account. Raw fetch, not api(): api() calls logout() on a 401, which would loop.
function endSession(path) {
  if (!token) return;
  fetch(API + path, { method: "POST", keepalive: true, body: "{}",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token } }).catch(() => {});
}
function logout(serverPath = null) {
  stopTracking();
  stopDirect();
  if (serverPath) dropPush();  // this phone must not get the next account's trip requests
  if (serverPath) endSession(serverPath);
  token = null; refreshToken = null;
  localStorage.removeItem("ap_driver_token");
  localStorage.removeItem("ap_driver_refresh");
  show("loginView");
}
$("logout").onclick = () => logout("/auth/logout");
$("logoutAll").onclick = () => {
  if (confirm("Sign out on every phone and computer that uses this account?")) logout("/auth/logout-all");
};

$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  $("loginErr").textContent = "";
  const f = new FormData(e.target);
  try {
    const r = await api("/auth/login", { method: "POST", body: JSON.stringify({ email: f.get("email"), password: f.get("password") }) });
    if (r.user.role !== "driver") throw new Error("This app is for drivers. Use the web dashboard for other roles.");
    saveTokens(r);
    loadTrips();
    enablePush(false);
  } catch (err) { $("loginErr").textContent = err.message; }
};

const fmtTime = (s) => (s ? new Date(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "–");

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function loadRequests() {
  const box = $("requests");
  if (!box) return;
  const reqs = await api("/driver/bookings");
  box.innerHTML = reqs.length ? "<h2>New booking requests</h2>" : "";
  for (const r of reqs) {
    const d = document.createElement("div");
    d.className = "card";
    d.innerHTML = `<b>${esc(r.farmer)}</b> · ${esc(r.village || "farm")}${r.farmer_phone ? ` · <a href="tel:${esc(r.farmer_phone)}">${esc(r.farmer_phone)}</a>` : ""}<br>
      <span class="meta">${esc(r.crop)} ${esc(r.tons)} t → ${esc(r.mandi)} · pickup ${esc(r.pickup_local)} · ~${esc(Math.round(r.road_km_approx))} km</span>`;
    const b = document.createElement("button");
    b.textContent = "Accept job";
    b.onclick = async () => { b.disabled = true; try { await api(`/driver/bookings/${r.id}/accept`, { method: "POST" }); loadTrips(); } catch (e) { alert(e.message); b.disabled = false; } };
    d.appendChild(b);
    box.appendChild(d);
  }
}

async function loadTrips() {
  show("listView");
  loadRequests().catch(() => {});
  loadAvailability().catch(() => {});
  loadDirect().catch(() => {});
  openLive();
  const trips = await api("/trips?status=assigned,accepted,in_progress,completed");
  $("trips").innerHTML = trips.length ? "" : '<p class="meta">No trips assigned yet.</p>';
  for (const t of trips) {
    const b = document.createElement("button");
    b.className = "trip secondary";
    b.innerHTML = `<b>${t.vehicle}</b> → ${t.mandi} <br>
      <span class="meta">${t.status} · ${t.load_tons} t · ${t.planned_distance_km ?? "?"} km</span>`;
    b.onclick = () => openTrip(t.id);
    $("trips").appendChild(b);
  }
  // resume tracking after a reload if a trip is live
  const live = trips.find((t) => t.status === "in_progress" && t.consent_given_at);
  if (live && tracking === null) startTracking(live.id);
}
$("refresh").onclick = loadTrips;
$("back").onclick = loadTrips;

async function openTrip(id) {
  current = await api(`/trips/${id}`);
  render();
  show("tripView");
}

function render() {
  const t = current;
  $("tTitle").innerHTML = `${t.vehicle} → ${t.mandi} `;
  $("codeBox").hidden = !(t.pickup_code && ["accepted", "in_progress"].includes(t.status));
  $("pickupBox").innerHTML = (t.pickups || []).map((p) => `<p><b>Pick up:</b> ${esc(p.farmer)}${p.village ? `, ${esc(p.village)}` : ""}
    ${p.phone ? ` · <a href="tel:${esc(p.phone)}">${esc(p.phone)}</a>` : ""}<br><span class="meta">${esc(p.crop)} ${esc(p.tons)} t ·
    <a target="_blank" rel="noreferrer" href="https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=15/${p.lat}/${p.lon}">farm on the map</a></span></p>`).join("");
  renderMap(t);
  if (t.pickup_code) $("pickupCode").textContent = t.pickup_code;
  $("tMeta").textContent = `Status: ${t.status} · load ${t.load_tons} t · planned ${t.planned_distance_km ?? "?"} km`
    + (t.route_source === "haversine" ? " (approximate route)" : "");
  $("consentBox").hidden = !["accepted", "in_progress"].includes(t.status);
  $("checklist").hidden = $("consentBox").hidden;
  $("consent").checked = !!t.consent_given_at;
  $("scanBox").hidden = !(t.status === "in_progress" && !t.pickup_scanned_at && t.shipment_id);
  $("deliveryBox").hidden = !(t.status === "in_progress" && t.delivery_qr_token);
  if (!$("deliveryBox").hidden) drawQR(t.delivery_qr_token);
  $("tRemaining").textContent = t.remaining_km != null ? `${t.remaining_km.toFixed(1)} km` : "–";
  $("tEta").textContent = t.eta_local || "–";
  $("tFix").textContent = fmtTime(t.last_seen_at);

  const a = $("actions");
  a.innerHTML = "";
  const btn = (label, fn, cls = "") => { const b = document.createElement("button"); b.textContent = label; b.className = cls; b.onclick = fn; a.appendChild(b); };
  if (t.status === "assigned") {
    btn("Accept trip", () => act("accept"));
    btn("Decline", () => act("decline"), "secondary");
  }
  if (t.status === "accepted") btn("Start trip", async () => {
    if (!$("consent").checked) return alert("Tick the location-sharing consent first.");
    await act("start");
    startTracking(current.id);
  });
  if (t.status === "in_progress" && t.shipment_id && !t.delivery_scanned_at) {
    const p = document.createElement("p");
    p.className = "meta";
    p.textContent = "End trip unlocks after the trader scans your delivery QR. To stop sharing location now, untick location sharing above.";
    a.appendChild(p);
  }
  if (t.status === "in_progress" && (!t.shipment_id || t.delivery_scanned_at)) btn("End trip", async () => {
    if (!confirm("End the trip and stop sharing location?")) return;
    await flush();
    await act("end");
    stopTracking();
  }, "danger");
}


// Where to drive now: the farm until the load is handed over, then the mandi. The map is redrawn only when the
// destination changes (render() runs on every live update). Directions open Google Maps / the phone's maps app.
let mapKey = null;
function renderMap(t) {
  const box = $("mapBox");
  const p = (t.pickups || [])[0];
  const toFarm = !t.pickup_scanned_at && p && p.lat != null;
  const dest = toFarm ? { lat: p.lat, lon: p.lon, name: `${p.farmer}'s farm${p.village ? `, ${p.village}` : ""}` }
    : t.mandi_lat != null ? { lat: t.mandi_lat, lon: t.mandi_lon, name: t.mandi } : null;
  const open = ["assigned", "accepted", "in_progress"].includes(t.status);
  if (!dest || !open) { box.hidden = true; mapKey = null; return; }
  const key = `${t.id}:${dest.lat}:${dest.lon}`;
  box.hidden = false;
  if (key === mapKey) return;
  mapKey = key;
  const d = 0.012;
  const embed = `https://www.openstreetmap.org/export/embed.html?bbox=${dest.lon - d * 1.6},${dest.lat - d},${dest.lon + d * 1.6},${dest.lat + d}&layer=mapnik&marker=${dest.lat},${dest.lon}`;
  const nav = `https://www.google.com/maps/dir/?api=1&destination=${dest.lat},${dest.lon}&travelmode=driving`;
  box.innerHTML = `<p><b>${toFarm ? "Go to the farm" : "Go to the mandi"}:</b> ${esc(dest.name)}</p>
    <iframe class="map" title="Destination map" loading="lazy" src="${embed}"></iframe>
    <a class="btn" target="_blank" rel="noreferrer" href="${nav}">🧭 Directions to ${toFarm ? "the farm" : esc(dest.name)}</a>
    <p class="meta">${esc(dest.lat.toFixed(5))}, ${esc(dest.lon.toFixed(5))}${toFarm && p.phone ? ` · call the farmer: <a href="tel:${esc(p.phone)}">${esc(p.phone)}</a>` : ""}</p>
    ${NATIVE ? "" : '<p class="meta">In the browser app, location sharing pauses while Maps is in front. Check the route, then come back here and keep this screen open while driving.</p>'}`;
}

async function act(action) {
  try { current = await api(`/trips/${current.id}/${action}`, { method: "POST" }); render(); }
  catch (e) { alert(e.message); }
}

$("consent").onchange = async (e) => {
  try { current = await api(`/trips/${current.id}/consent`, { method: "POST", body: JSON.stringify({ consent: e.target.checked }) }); render(); }
  catch (err) { alert(err.message); }
  if (!e.target.checked) stopTracking();
};

function notice(text, warn = false) {
  $("notice").textContent = text;
  $("notice").className = "notice" + (warn ? " warn" : "");
  $("notice").hidden = false;
  setTimeout(() => { $("notice").hidden = true; }, warn ? 20000 : 8000);
}

// ------------------------------------------------------------ QR
function drawQR(text) {
  if (!window.qrcode) { $("qr").textContent = ""; $("qrText").textContent = text; return; }
  const q = qrcode(0, "M");
  q.addData(text);
  q.make();
  $("qr").innerHTML = q.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
  $("qrText").textContent = text;
}

async function submitPickup(tokenText) {
  try {
    current = await api(`/trips/${current.id}/scan/pickup`, { method: "POST", body: JSON.stringify({ token: tokenText.trim() }) });
    render();
    notice("Pickup confirmed. The farmer has been notified.");
  } catch (e) { $("scanMsg").textContent = e.message; }
}
$("manualBtn").onclick = () => submitPickup($("manualToken").value);
$("scanBtn").onclick = async () => {
  if (!("BarcodeDetector" in window)) { $("scanMsg").textContent = "This browser can't scan QR codes; type the code instead."; return; }
  const video = $("video");
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
  video.srcObject = stream; video.hidden = false; await video.play();
  const detector = new BarcodeDetector({ formats: ["qr_code"] });
  const loop = async () => {
    const codes = await detector.detect(video).catch(() => []);
    if (codes.length) {
      stream.getTracks().forEach((t) => t.stop()); video.hidden = true;
      return submitPickup(codes[0].rawValue);
    }
    requestAnimationFrame(loop);
  };
  loop();
};

// ------------------------------------------------------------ tracking
function recordFix(tripId, f) {
  // f = {time (ms), lat, lon, speed_mps, accuracy}
  if (tracking !== tripId || f.time - lastFixMs < MIN_FIX_MS) return;
  lastFixMs = f.time;
  const p = {
    k: `${tripId}:${f.time}`, trip: tripId,
    recorded_at: new Date(f.time).toISOString(),
    lat: f.lat, lon: f.lon,
    speed_kmph: f.speed_mps != null ? f.speed_mps * 3.6 : null,
    accuracy_m: f.accuracy,
  };
  bufAdd(p).then(flush);
}

async function startTracking(tripId) {
  if (tracking !== null) return;
  tracking = tripId;
  lastFixMs = 0;
  $("trackingBar").hidden = false;
  $("trackingTrip").textContent = "#" + tripId;
  openSocket(tripId);
  if (NATIVE) return startNative(tripId);
  if (!("geolocation" in navigator)) { stopTracking(); return alert("This phone has no GPS access in the browser."); }
  await holdScreen();
  watchId = navigator.geolocation.watchPosition(
    (pos) => recordFix(tripId, { time: pos.timestamp, lat: pos.coords.latitude, lon: pos.coords.longitude,
      speed_mps: pos.coords.speed, accuracy: pos.coords.accuracy }),
    (err) => { $("tFix").textContent = "GPS error: " + err.message; },
    { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
  );
}

async function startNative(tripId) {
  // Android 13+: the plugin doesn't ask for notifications, and without them the "tracking on" notice is hidden.
  try { await plugin("LocalNotifications").requestPermissions(); } catch { /* older Android: not needed */ }
  try {
    const id = await BG.addWatcher({
      backgroundTitle: "AgriPulse: tracking on",
      backgroundMessage: `Sharing your location for trip #${tripId} until you end the trip.`,
      requestPermissions: true, stale: false, distanceFilter: 0,
    }, (loc, err) => {
      if (err) {
        $("tFix").textContent = "GPS error: " + err.message;
        if (err.code === "NOT_AUTHORIZED") notice("Location permission is off. Tap 'Open app settings' and allow location.", true);
        return;
      }
      if (loc) recordFix(tripId, { time: loc.time ?? Date.now(), lat: loc.latitude, lon: loc.longitude,
        speed_mps: loc.speed, accuracy: loc.accuracy });
    });
    if (tracking === tripId) nativeWatcher = id; else BG.removeWatcher({ id }); // stopped while starting
  } catch (e) { $("tFix").textContent = "GPS error: " + e.message; }
}

function stopTracking() {
  if (watchId !== null) navigator.geolocation.clearWatch(watchId);
  if (nativeWatcher !== null) BG.removeWatcher({ id: nativeWatcher }).catch(() => {});
  watchId = null;
  nativeWatcher = null;
  tracking = null;
  hiddenAt = null;
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  releaseScreen();
  $("trackingBar").hidden = true;
  if (avail?.online) holdScreen(); // still online for direct bookings: keep the screen on for incoming requests
}

// The server refused points: the trip ended or consent was withdrawn elsewhere. Stop GPS now (privacy rule 4).
async function trackingRefused(tripId, why) {
  const all = await bufAll();
  await bufDel(all.filter((p) => String(p.trip) === String(tripId)).map((p) => p.k));
  if (String(tracking) === String(tripId)) stopTracking();
  notice(`Location sharing stopped: ${why}`, true);
}

// Browser only. The browser drops the screen lock whenever the page is hidden, so it is re-requested every time
// the page comes back (B-2 finding: the old code asked once, and after one phone call the screen went to sleep).
async function holdScreen() {
  if (NATIVE || (tracking === null && !avail?.online) || wakeLock || document.visibilityState !== "visible") return;
  try {
    wakeLock = await navigator.wakeLock?.request("screen");
    wakeLock?.addEventListener("release", () => { wakeLock = null; });
  } catch { wakeLock = null; /* not supported, or battery saver */ }
}
function releaseScreen() {
  const w = wakeLock;
  wakeLock = null;
  w?.release?.().catch?.(() => {});
}

// Browser only: the page is about to stop running, so tell the server location is paused (not "vehicle stopped").
function reportPause(tripId) {
  fetch(API + `/trips/${tripId}/pause`, {
    method: "POST", keepalive: true,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: JSON.stringify({ reason: "screen_off", at: new Date().toISOString() }),
  }).catch(() => { /* offline: the monitor will call it "no signal" instead */ });
}

async function openSocket(tripId) {
  // V3-3: a 60-second single-use ticket, so the access token never goes in a URL (proxies log URLs)
  let ticket;
  try { ticket = (await api("/auth/ws-ticket", { method: "POST" })).ticket; } catch { ticket = null; }
  if (tracking !== tripId) return;
  if (!ticket) { setTimeout(() => { if (tracking === tripId) openSocket(tripId); }, 10000); return; }
  const url = API.replace(/^http/, "ws") + `/ws/driver/${tripId}?ticket=${encodeURIComponent(ticket)}`;
  ws = new WebSocket(url);
  ws.onopen = () => flush();
  ws.onmessage = (m) => {
    const r = JSON.parse(m.data);
    if (r.ok === false) { trackingRefused(tripId, r.error); return; }
    if (r.trip && current && current.id === tripId) { Object.assign(current, r.trip); render(); }
  };
  // 4403 = token rejected (usually expired): refresh first, then reconnect
  ws.onclose = async (ev) => {
    ws = null;
    if (tracking !== tripId) return;
    if (ev.code === 4403) await tryRefresh();
    setTimeout(() => openSocket(tripId), 3000);
  };
}

// Flush everything buffered. Socket if open, otherwise HTTP. Anything that fails stays queued.
async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    const all = await bufAll();
    updateQueue(all.length);
    if (!all.length || !navigator.onLine) return;
    const byTrip = {};
    for (const p of all) (byTrip[p.trip] ||= []).push(p);
    for (const [tripId, pts] of Object.entries(byTrip)) {
      for (let i = 0; i < pts.length; i += 500) {
        const chunk = pts.slice(i, i + 500);
        const payload = chunk.map(({ recorded_at, lat, lon, speed_kmph, accuracy_m }) => ({ recorded_at, lat, lon, speed_kmph, accuracy_m }));
        // the socket only while on screen: in the background (Android app) Android throttles it; HTTP is native there
        if (ws && ws.readyState === 1 && String(current?.id) === tripId && document.visibilityState === "visible") {
          ws.send(JSON.stringify({ points: payload }));
        } else {
          try {
            await api(`/trips/${tripId}/points`, { method: "POST", body: JSON.stringify({ points: payload }) });
          } catch (e) {
            if (e.status === 409) { await trackingRefused(tripId, e.message); break; } // this trip's queue is already dropped
            throw e;
          }
        }
        await bufDel(chunk.map((p) => p.k));
      }
    }
    updateQueue((await bufAll()).length);
  } catch (e) {
    // offline or server error: keep the points, try again on the next fix / reconnect
  } finally { flushing = false; }
}
function updateQueue(n) {
  $("tQueued").textContent = n;
  $("queue").textContent = n ? `${n} queued` : "";
}

function netState() {
  $("net").textContent = navigator.onLine ? "online" : "offline";
  $("net").className = "pill" + (navigator.onLine ? "" : " off");
  if (navigator.onLine) flush();
}
window.addEventListener("online", netState);
window.addEventListener("offline", netState);
document.addEventListener("visibilitychange", () => {
  if (tracking === null || NATIVE) return; // the Android app keeps recording in the background
  if (document.visibilityState === "hidden") {
    if (hiddenAt === null) { hiddenAt = Date.now(); reportPause(tracking); }
    return;
  }
  holdScreen();
  if (hiddenAt !== null) {
    const min = Math.round((Date.now() - hiddenAt) / 60000);
    hiddenAt = null;
    if (min >= 1) notice(`Location was paused for ${min} min while this app was off screen. Keep it open on screen while driving.`, true);
  }
});


// ------------------------------------------------------------ direct farmer bookings (real accounts, real phones)
// The driver goes online for a district + mandis with their own truck. A farmer's request reaches this phone over the
// /ws/live socket (instant while the app is open) and as a Web Push notification (when the screen is locked); the
// 15-second poll of /driver/requests is the fallback AND the check-in that keeps the driver matched.
let avail = null;
let liveWs = null;
let livePending = false;
let directPoll = null;
let directTick = null;
const knownReqs = new Set();
const POLL_MS = 15000;

async function loadAvailability() {
  const a = await api("/driver/availability");
  if (!a || !Array.isArray(a.vehicles)) return;
  avail = a;
  renderAvailability();
  syncOnline();
}

function renderAvailability(district) {
  const a = avail;
  const box = $("availBox");
  box.hidden = false;
  if (a.demo_account) {
    box.innerHTML = '<p class="meta">Direct bookings from farmers are for real accounts. Sign up with your own driver account (your fleet owner adds your phone number) to receive them.</p>';
    return;
  }
  const d = district ?? a.district ?? "";
  const chosen = new Set(a.mandi_ids);
  const names = a.mandis.filter((m) => chosen.has(m.id)).map((m) => m.name).join(", ");
  const status = a.online
    ? (a.matched_now ? `<p class="status on">● Online: farmers in ${esc(a.district)} sending to ${esc(names)} can book you</p>`
                     : `<p class="status warn">● Online, but this phone hasn't checked in recently. Keep the app open${a.push_subscribed ? "" : " or turn on notifications"}.</p>`)
    : '<p class="status off">○ Offline: you won\'t get direct bookings</p>';
  const push = !("PushManager" in window) || NATIVE
    ? '<p class="meta">This app can\'t show notifications on this phone: keep it open on screen while online.</p>'
    : a.push_subscribed && Notification.permission === "granted"
      ? `<p class="meta">🔔 Notifications on: requests reach this phone even when the screen is locked (you stay matched for ${Math.round(a.stays_online_min / 60)} h without opening the app).</p>`
      : '<button id="pushBtn" class="secondary" type="button">🔔 Turn on notifications</button><p class="meta">So a request reaches you when the screen is locked.</p>';
  box.innerHTML = `<h2 style="margin:0 0 6px;font-size:1.1rem">Direct bookings</h2>${status}
    <label>District <select id="avDistrict"><option value="">Choose…</option>${a.districts.map((x) =>
      `<option ${x === d ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></label>
    <div class="meta">Mandis you will deliver to</div>
    <div class="mandis">${a.mandis.filter((m) => m.district === d).map((m) =>
      `<label><input type="checkbox" value="${m.id}" ${chosen.has(m.id) ? "checked" : ""}> ${esc(m.name)}</label>`).join("") || '<span class="meta">Choose a district first</span>'}</div>
    <label>Truck you are driving today <select id="avVehicle">${a.vehicles.length ? a.vehicles.map((v) =>
      `<option value="${v.id}" ${v.id === a.vehicle_id ? "selected" : ""}>${esc(v.registration)} · ${esc(v.capacity_tons)} t</option>`).join("")
      : '<option value="">No truck in your company yet</option>'}</select></label>
    <div class="row">${a.online
      ? '<button id="avSave" type="button">Save</button><button id="avOff" class="secondary" type="button">Go offline</button>'
      : '<button id="avOn" type="button">Go online</button>'}</div>
    <p id="avErr" class="err"></p>${push}`;
  $("avDistrict").onchange = (e) => renderAvailability(e.target.value);
  const save = async (online) => {
    $("avErr").textContent = "";
    const mandi_ids = [...box.querySelectorAll(".mandis input:checked")].map((i) => +i.value);
    try {
      avail = await api("/driver/availability", { method: "PUT", body: JSON.stringify({
        online, district: $("avDistrict").value || null, mandi_ids, vehicle_id: +$("avVehicle").value || null }) });
      renderAvailability();
      syncOnline();
      if (online) primeSound();
    } catch (e) { $("avErr").textContent = e.message; }
  };
  if ($("avOn")) $("avOn").onclick = () => save(true);
  if ($("avSave")) $("avSave").onclick = () => save(true);
  if ($("avOff")) $("avOff").onclick = () => save(false);
  if ($("pushBtn")) $("pushBtn").onclick = () => enablePush(true);
}

function syncOnline() {
  if (avail?.online) {
    if (!directPoll) directPoll = setInterval(() => loadDirect().catch(() => {}), POLL_MS);
    holdScreen();
    openLive();
  } else {
    clearInterval(directPoll); directPoll = null;
    if (tracking === null) releaseScreen();
  }
}

function stopDirect() {
  clearInterval(directPoll); directPoll = null;
  clearInterval(directTick); directTick = null;
  avail = null;
  if (liveWs) { liveWs.onclose = null; liveWs.close(); liveWs = null; }
  $("availBox").hidden = true;
  $("directReqs").innerHTML = "";
}

async function loadDirect() {
  if (!token) return;
  const list = await api("/driver/requests");
  if (!Array.isArray(list)) return;
  const box = $("directReqs");
  let fresh = false;
  box.innerHTML = "";
  for (const r of list) {
    if (!knownReqs.has(r.request_id)) { knownReqs.add(r.request_id); fresh = true; }
    const d = document.createElement("div");
    d.className = "card req";
    d.innerHTML = `<h2>🚚 New trip request</h2>
      <b>${esc(r.farmer)}</b>${r.village ? ` · ${esc(r.village)}` : ""}${r.farmer_district ? ` (${esc(r.farmer_district)})` : ""}
      <dl><dt>Produce</dt><dd>${esc(r.crop)} · ${esc(r.tons)} t${r.grade ? ` · ${esc(r.grade)}` : ""}</dd>
      <dt>Deliver to</dt><dd>${esc(r.mandi)} (${esc(r.mandi_district)})</dd>
      <dt>Distance</dt><dd>~${esc(Math.round(r.road_km))} km farm → mandi</dd>
      <dt>Your pay (est.)</dt><dd>₹${esc(Number(r.driver_pay_estimate).toLocaleString("en-IN"))}</dd>
      <dt>Answer within</dt><dd class="countdown" data-exp="${esc(r.expires_at)}">–</dd></dl>
      <a class="meta" target="_blank" rel="noreferrer" href="https://www.openstreetmap.org/?mlat=${esc(r.pickup_lat)}&mlon=${esc(r.pickup_lon)}#map=14/${esc(r.pickup_lat)}/${esc(r.pickup_lon)}">Farm location on the map</a>
      <div class="row" style="margin-top:10px"></div><p class="err"></p>`;
    const acc = document.createElement("button");
    acc.textContent = "Accept";
    const dec = document.createElement("button");
    dec.textContent = "Decline";
    dec.className = "secondary";
    const err = d.querySelector(".err");
    acc.onclick = async () => {
      acc.disabled = dec.disabled = true;
      try {
        const trip = await api(`/driver/requests/${r.request_id}/accept`, { method: "POST" });
        notice("Trip accepted. The farmer can see you now. Tick location sharing and start when you leave.");
        await loadTrips();
        openTrip(trip.id);
      } catch (e) { err.textContent = e.message; loadDirect().catch(() => {}); }
    };
    dec.onclick = async () => {
      acc.disabled = dec.disabled = true;
      try { await api(`/driver/requests/${r.request_id}/decline`, { method: "POST" }); } catch (e) { err.textContent = e.message; }
      loadDirect().catch(() => {});
    };
    d.querySelector(".row").append(acc, dec);
    box.appendChild(d);
  }
  if (fresh) alertDriver(list[0]);
  tickCountdowns();
  if (list.length && !directTick) directTick = setInterval(tickCountdowns, 1000);
  if (!list.length) { clearInterval(directTick); directTick = null; }
}

function tickCountdowns() {
  let expired = false;
  for (const el of document.querySelectorAll("[data-exp]")) {
    const left = Math.max(0, Math.round((new Date(el.dataset.exp) - Date.now()) / 1000));
    el.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    if (left === 0) expired = true;
  }
  if (expired) loadDirect().catch(() => {});
}

// Sound + vibration for a new request. Browsers allow audio only after a tap, so "Go online" primes it.
let audioCtx = null;
function primeSound() {
  try { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); audioCtx.resume?.(); } catch { audioCtx = null; }
}
function alertDriver(r) {
  try { navigator.vibrate?.([400, 200, 400, 200, 400]); } catch {}
  try {
    if (audioCtx) {
      for (const [at, f] of [[0, 880], [0.25, 660], [0.5, 880]]) {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = f; o.connect(g); g.connect(audioCtx.destination);
        g.gain.setValueAtTime(0.25, audioCtx.currentTime + at);
        o.start(audioCtx.currentTime + at); o.stop(audioCtx.currentTime + at + 0.2);
      }
    }
  } catch {}
  if (r && document.visibilityState === "visible") notice(`New trip request: ${r.crop} ${r.tons} t → ${r.mandi}`);
}

// /ws/live: the server pushes "trip_request" / "trip_request_closed" on this driver's own channel.
async function openLive() {
  if (liveWs || livePending || !token) return;
  livePending = true;
  let ticket = null;
  try { ticket = (await api("/auth/ws-ticket", { method: "POST" })).ticket; } catch { ticket = null; }
  livePending = false;
  if (!token || liveWs) return;
  if (!ticket) { setTimeout(openLive, 10000); return; }
  try { liveWs = new WebSocket(API.replace(/^http/, "ws") + `/ws/live?ticket=${encodeURIComponent(ticket)}`); }
  catch { liveWs = null; setTimeout(openLive, 10000); return; }
  liveWs.onmessage = (m) => {
    let msg; try { msg = JSON.parse(m.data); } catch { return; }
    if (msg.type === "trip_request" || msg.type === "trip_request_closed") loadDirect().catch(() => {});
  };
  liveWs.onclose = () => { liveWs = null; if (token) setTimeout(openLive, 5000); };
}

// Web Push: subscribe this phone (the API generates its VAPID key once). `ask` = the driver tapped the button.
function b64urlToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
async function enablePush(ask) {
  if (NATIVE || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return;
  try {
    if (ask && Notification.permission !== "granted") {
      if (await Notification.requestPermission() !== "granted") {
        notice("Notifications are blocked. Allow them in the browser's site settings, or keep this app open while online.", true);
        return;
      }
    }
    if (Notification.permission !== "granted") return;
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      const { key } = await api("/push/vapid-public-key");
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(key) });
    }
    await api("/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
    if (ask) { notice("Notifications are on for trip requests."); loadAvailability().catch(() => {}); }
  } catch (e) { if (ask) notice("Couldn't turn on notifications: " + e.message, true); }
}
function dropPush() {
  if (!token || NATIVE || !("serviceWorker" in navigator)) return;
  const t = token;
  navigator.serviceWorker.ready.then((reg) => reg.pushManager?.getSubscription()).then((sub) => {
    if (!sub) return;
    fetch(API + "/push/unsubscribe", { method: "POST", keepalive: true, body: JSON.stringify({ endpoint: sub.endpoint }),
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + t } }).catch(() => {});
  }).catch(() => {});
}
// back on screen (after a lock / a notification tap): fetch requests at once and re-take the screen lock
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !token) return;
  if (avail?.online) { holdScreen(); loadDirect().catch(() => {}); openLive(); }
});
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.type === "open_requests" && token) loadTrips().catch(() => {});
  });
}

$("openSettings").onclick = () => BG?.openSettings();
$("checkWeb").hidden = NATIVE;
$("checkNative").hidden = !NATIVE;
$("footWeb").hidden = NATIVE;
// The Android app serves these files itself; a service worker would only get in the way there.
if (!NATIVE && "serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
netState();
async function boot() {
  const h = new URLSearchParams(location.hash.slice(1));
  const ticket = h.get("handoff");
  const tripId = h.get("trip");
  if (ticket || tripId) history.replaceState(null, "", location.pathname + location.search); // drop it from the address bar
  if (ticket) {
    try {
      const r = await api("/auth/handoff", { method: "POST", body: JSON.stringify({ ticket }) });
      if (r.user.role !== "driver") throw new Error("This app is for drivers.");
      if (token && tracking !== null) stopTracking();
      saveTokens(r);
    } catch (e) { if (!token) $("loginErr").textContent = e.message; }
  }
  if (!token) { show("loginView"); return; }
  try {
    await loadTrips();
    enablePush(false);
    if (tripId) await openTrip(+tripId);
  } catch { if (!token) show("loginView"); }
}
boot();
