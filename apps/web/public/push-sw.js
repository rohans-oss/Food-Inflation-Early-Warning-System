// Web Push only (no caching): a farmer's trip request reaches a driver's phone even when the screen is locked.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || "New trip request", {
      body: d.body || "Open AgriPulse to accept or decline.", tag: d.tag || "agripulse-trip", renotify: true,
      requireInteraction: true, vibrate: [400, 200, 400, 200, 400], data: { url: "/driver" },
    }),
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((cs) => cs.forEach((c) => c.postMessage({ type: "open_requests" }))),
  ]));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    const open = cs.find((c) => new URL(c.url).pathname.startsWith("/driver"));
    if (open) { open.postMessage({ type: "open_requests" }); return open.focus(); }
    return self.clients.openWindow("/driver");
  }));
});
