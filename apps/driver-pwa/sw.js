// App-shell cache so the driver app opens with no signal. API calls are never cached.
const CACHE = "agripulse-driver-v7";
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
