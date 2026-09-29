/* AgriPulse driver PWA.
 * - GPS only while a trip is in_progress AND the driver ticked consent (server enforces it too)
 * - every fix goes to IndexedDB first, then is flushed over WebSocket (or HTTP when the socket is down)
 * - re-sends are safe: the server de-duplicates on (trip, timestamp)
 */
// The PWA is served by the API at <api-root>/driver/, so the API root is the parent path:
// dev  http://localhost:8000/driver/     -> http://localhost:8000
// prod https://example.org/api/driver/   -> https://example.org/api   (behind Caddy)
const API = window.AGRIPULSE_API || new URL("..", location.href).href.replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
let token = localStorage.getItem("ap_driver_token");
let refreshToken = localStorage.getItem("ap_driver_refresh");
let current = null;      // trip being viewed
let watchId = null;      // geolocation watch
let ws = null;
let wakeLock = null;
let flushing = false;

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
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

// ------------------------------------------------------------ views
function show(view) {
  for (const v of ["loginView", "listView", "tripView"]) $(v).hidden = v !== view;
  $("logout").hidden = view === "loginView";
}
function logout() {
  stopTracking();
  token = null; refreshToken = null;
  localStorage.removeItem("ap_driver_token");
  localStorage.removeItem("ap_driver_refresh");
  show("loginView");
}
$("logout").onclick = logout;

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
  if (live && watchId === null) startTracking(live.id);
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
  if (t.status === "in_progress") btn("End trip", async () => {
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

function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = false;
  setTimeout(() => { $("notice").hidden = true; }, 8000);
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
async function startTracking(tripId) {
  if (watchId !== null) return;
  if (!("geolocation" in navigator)) return alert("This phone has no GPS access in the browser.");
  $("trackingBar").hidden = false;
  $("trackingTrip").textContent = "#" + tripId;
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* not supported */ }
  openSocket(tripId);
  watchId = navigator.geolocation.watchPosition(
    async (pos) => {
      const p = {
        k: `${tripId}:${pos.timestamp}`, trip: tripId,
        recorded_at: new Date(pos.timestamp).toISOString(),
        lat: pos.coords.latitude, lon: pos.coords.longitude,
        speed_kmph: pos.coords.speed != null ? pos.coords.speed * 3.6 : null,
        accuracy_m: pos.coords.accuracy,
      };
      await bufAdd(p);
      flush();
    },
    (err) => { $("tFix").textContent = "GPS error: " + err.message; },
    { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
  );
}

function stopTracking() {
  if (watchId !== null) navigator.geolocation.clearWatch(watchId);
  watchId = null;
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  wakeLock?.release?.();
  wakeLock = null;
  $("trackingBar").hidden = true;
}

function openSocket(tripId) {
  const url = API.replace(/^http/, "ws") + `/ws/driver/${tripId}?token=${encodeURIComponent(token)}`;
  ws = new WebSocket(url);
  ws.onopen = () => flush();
  ws.onmessage = (m) => {
    const r = JSON.parse(m.data);
    if (r.ok === false) { $("scanMsg").textContent = r.error; return; }
    if (r.trip && current && current.id === tripId) { Object.assign(current, r.trip); render(); }
  };
  // 4403 = token rejected (usually expired): refresh first, then reconnect
  ws.onclose = async (ev) => {
    ws = null;
    if (watchId === null) return;
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
        if (ws && ws.readyState === 1 && String(current?.id) === tripId) ws.send(JSON.stringify({ points: payload }));
        else await api(`/trips/${tripId}/points`, { method: "POST", body: JSON.stringify({ points: payload }) });
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
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && watchId !== null && !wakeLock) {
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}
  }
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
netState();
if (token) loadTrips().catch(() => show("loginView")); else show("loginView");
