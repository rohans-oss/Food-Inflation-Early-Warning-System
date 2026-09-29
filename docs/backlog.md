# Backlog: known issues outside the current phase

V2 rule 16: anything noticed that isn't the current phase's job goes here, not into that phase's code.
Each item says where it came from and why it's deferred.

## V1 hardening (not V2 scope)

| # | Item | Why it matters | Notes |
|---|---|---|---|
| 1 | **React Native driver app** | Browsers stop GPS when the screen locks or the driver switches app; the PWA can't fix that | Decide from field-test gap data (`docs/field-test.md` §4). Server API unchanged |
| 2 | **Kannada native-speaker review** | Alert + UI strings are machine-drafted (`kn_reviewed: false` in both i18n files) | `services/api/agripulse_api/i18n/alerts.json`, `apps/web/lib/messages.json` |
| 3 | **Mandi-map confirmation** | Seeded coordinates are town centroids (`coords_verified = false`); a wrong point means `reached_mandi` never fires | `PATCH /admin/mandis/{id}` per mandi. Graph distance edges in V2-3 use these points too |
| 4 | Driver can end a trip before the delivery QR is scanned | Lots stay `in_transit` forever; lender reliability undercounts | Either block `end` until delivery scan, or let the trader close the lot later |
| 5 | Refresh tokens can't be individually revoked | A stolen refresh token works until expiry unless the whole user is disabled | Needs a token table / `jti` denylist |
| 6 | JWT in WebSocket query string | Tokens can land in proxy logs (Caddy redacts them; other proxies may not) | Move to a short-lived WS ticket from an authenticated POST |
| 7 | Public OSM tile server | Its usage policy doesn't allow production traffic | Self-host tiles or use a provider before a pilot |
| 8 | Festival flags are approximate calendar windows | Lunar-calendar festivals move ±2 weeks year to year | Replace with a dated festival table |
| 9 | Multi-pickup trips use one tonnage-weighted point | ETA and geofence are wrong for spread-out FPO pickups | Multi-stop routing is V3 (OR-Tools) |
| 10 | Spike alert threshold (0.5) fires almost never | Spike recall ≈ 0 for every model on synthetic data | Choose the threshold from precision/recall on **real** data |
| 11 | `ingest.run synthetic` generates up to *today* | Every day's demo database is a different random draw, so demo backtests change daily | Pin the end date like `eval/baseline.py` does, or label the draw date |

## V3 (by design)

OR-Tools optimizer, scenario simulator, Hindi alerts, multilingual review workflow.
