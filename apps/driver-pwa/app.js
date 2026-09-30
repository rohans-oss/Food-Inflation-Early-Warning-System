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
  if (r.status === 401) { logout(); throw new Error("Session expired"); }
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
  } catch (err) { $("loginErr").textContent = err.message; }
};

const fmtTime = (s) => (s ? new Date(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "–");

async function loadTrips() {
  show("listView");
  const trips = await api("/trips?status=assigned,accepted,in_progress,completed");
  $("trips").innerHTML = trips.length ? "" : '<p class="meta">No trips assigned yet.</p>';
  for (const t of trips) {
    const b = document.createElement("button");
    b.className = "trip secondary";
    b.innerHTML = `<b>${t.vehicle}</b> → ${t.mandi} ${t.is_simulated ? '<span class="badge">Simulated</span>' : ""}<br>
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
  $("tTitle").innerHTML = `${t.vehicle} → ${t.mandi} ${t.is_simulated ? '<span class="badge">Simulated</span>' : ""}`;
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
  if (NATIVE || tracking === null || wakeLock || document.visibilityState !== "visible") return;
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

$("openSettings").onclick = () => BG?.openSettings();
$("checkWeb").hidden = NATIVE;
$("checkNative").hidden = !NATIVE;
$("footWeb").hidden = NATIVE;
// The Android app serves these files itself; a service worker would only get in the way there.
if (!NATIVE && "serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
netState();
if (token) loadTrips().catch(() => show("loginView")); else show("loginView");
