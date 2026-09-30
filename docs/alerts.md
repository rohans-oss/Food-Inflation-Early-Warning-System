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

Each user picks `preferred_lang` (`en`, `kn` or `hi`) at sign-up, in the header's language menu, or with
`PATCH /auth/me`. Alert text lives in `services/api/agripulse_api/i18n/alerts.json`; screen text lives in
`apps/web/lib/messages.json`.

> **Kannada and Hindi are machine-drafted** (V3-3). Each string has its own review status (`_review` in both files),
> and Admin → **Translations** shows how many are reviewed. Have a native speaker check them before any farmer sees
> them, especially numbers, units and the word for quintal.

**Review workflow**
1. `python -m agripulse_api.i18n_tools export --lang kn --out review_kn.csv` writes one row per string: English,
   current translation, and empty `corrected`, `approve` and `notes` columns. The file opens in Excel or Google
   Sheets with Kannada and Hindi intact.
2. The reviewer either writes a corrected translation or puts `yes` in `approve`.
3. `python -m agripulse_api.i18n_tools import --lang kn review_kn.csv --reviewer "Name"` applies the sheet and marks
   each touched string `reviewed`, with the reviewer's name and the date.
   - Every `{placeholder}` must match English exactly. If one doesn't, **the whole sheet is refused** and the error
     names the line, so a typo can never break an alert.
4. `pytest tests/test_i18n.py` then checks that every alert and screen string exists in all three languages with
   matching placeholders.
