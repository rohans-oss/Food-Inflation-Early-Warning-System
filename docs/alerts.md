# Alerts (V1)

| Kind | Fires when | Who gets it |
|---|---|---|
| `price_spike` | Latest 2-week forecast has spike probability ≥ `SPIKE_ALERT_PROBABILITY` (default 0.5) | Buyers (watched mandis), traders (their mandi), farmers / FPOs (mandis they ship to), policy, admin |
| `picked_up` | Driver scans the pickup QR | Farmers on the trip (includes the live tracking link) |
| `incoming_vehicle` | Pickup scanned | Traders at the destination mandi |
| `vehicle_delay` | ETA is ≥ `DELAY_ALERT_MINUTES` (45) past the planned arrival; at most once per extra hour | Farmers, FPO desk, fleet owner |
| `unexpected_stop` | Stationary > 30 min away from pickup and mandi, or the phone went silent that long | FPO desk, fleet owner |
| `vehicle_arrived` | Mandi geofence entered, or delivery QR scanned | Farmers, FPO, fleet owner, traders |
| `delivered` | Trader records weight and price | The farmer |

Every alert is stored once per user and event (`dedupe_key`), shown in-app (`GET /alerts` plus a live `user:{id}` WebSocket channel),
and sent by:

- **email** when `SMTP_HOST` is set, and
- **SMS / WhatsApp** through `SMS_WEBHOOK_URL`. The API POSTs `{"to": phone, "text": body}` so you can point it at any
  provider's relay. Nothing is sent if it's unset.

The channel outcome is recorded per alert (`sent`, `failed`, `not_configured`).

## Languages

Each user picks `preferred_lang` (`en` or `kn`) at sign-up or with `PATCH /auth/me`. Templates live in
`services/api/agripulse_api/alerts.py`.

> **The Kannada strings are machine-drafted and not yet reviewed** (`KN_REVIEWED = False`). Have a native speaker check
> them, especially numbers, units and the word for quintal, before any farmer sees them. Hindi arrives in V3.
