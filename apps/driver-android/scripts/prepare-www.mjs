// Copies the driver PWA (apps/driver-pwa) into www/ and writes config.js with the API address.
// The Android app serves these files from https://localhost, so it can't find the API from its own URL.
//   AGRIPULSE_API=https://<domain>/api npm run sync
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "driver-pwa");
const out = join(here, "..", "www");
const api = (process.env.AGRIPULSE_API || "").replace(/\/$/, "");

if (!/^https:\/\/[^/]+/.test(api) && !process.env.ALLOW_HTTP_API) {
  console.error("Set AGRIPULSE_API to the API's https URL, e.g. AGRIPULSE_API=https://agripulse.example.org/api");
  console.error("(phones refuse GPS and mixed content on plain http; ALLOW_HTTP_API=1 overrides for an emulator)");
  process.exit(1);
}
if (existsSync(out)) rmSync(out, { recursive: true });
mkdirSync(out, { recursive: true });
// sw.js is left out: the app ships its own files, a service worker would only cache stale copies
for (const f of ["index.html", "app.js", "app.css", "icon.svg", "manifest.webmanifest"]) cpSync(join(src, f), join(out, f));
writeFileSync(join(out, "config.js"), `// written by apps/driver-android/scripts/prepare-www.mjs\nwindow.AGRIPULSE_API = ${JSON.stringify(api)};\n`);
console.log(`www/ ready: API = ${api}`);
