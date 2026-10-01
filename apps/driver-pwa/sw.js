// App-shell cache so the driver app opens with no signal. API calls are never cached.
const CACHE = "agripulse-driver-v10";
const SHELL = ["./", "index.html", "config.js", "app.js", "app.css", "manifest.webmanifest", "icon.svg",
  "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const isShell = e.request.method === "GET" && (SHELL.includes(url.href) || (url.origin === location.origin && url.pathname.startsWith(new URL("./", location).pathname) && !url.pathname.includes("/trips")));
  if (!isShell) return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});

// Web Push: a farmer's trip request reaches the phone even when the screen is locked or the app is closed.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  const url = new URL("./" + (d.request_id ? "?request=" + d.request_id : ""), self.registration.scope).href;
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || "New trip request", {
      body: d.body || "Open AgriPulse Driver to accept or decline.", tag: d.tag || "agripulse-trip", renotify: true,
      requireInteraction: true, vibrate: [400, 200, 400, 200, 400], icon: "icon.svg", badge: "icon.svg", data: { url },
    }),
    // an open app refreshes its list at once
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((cs) => cs.forEach((c) => c.postMessage({ type: "open_requests" }))),
  ]));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data && e.notification.data.url;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    const open = cs.find((c) => c.url.startsWith(self.registration.scope));
    if (open) { open.postMessage({ type: "open_requests" }); return open.focus(); }
    return self.clients.openWindow(url || self.registration.scope);
  }));
});
