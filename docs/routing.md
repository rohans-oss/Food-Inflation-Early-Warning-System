# Routing, ETA and geofences

## OSRM (self-hosted)

The public OSRM demo server is not for production use, so AgriPulse runs its own.

```bash
bash infra/osrm/prepare.sh                 # one-time: download the South India extract + build (MLD). Needs ~8 GB RAM.
docker compose --profile routing up -d osrm
echo "OSRM_URL=http://osrm:5000" >> .env   # or http://localhost:5000 when the API runs outside Docker
docker compose restart api worker
```

Check the extract name on <https://download.geofabrik.de/asia/india.html> first (`PBF_URL=... bash infra/osrm/prepare.sh`
to override). Verify with:

```bash
curl "http://localhost:5000/route/v1/driving/78.25,13.10;78.13,13.137?overview=false"   # expect "code":"Ok"
```

**Without OSRM** (`OSRM_URL` empty) the API falls back to straight-line distance × `FALLBACK_ROAD_FACTOR` at `FALLBACK_SPEED_KMPH`. Every
trip and recommendation stores `route_source = "haversine"`, and the UI shows the route dashed with an "approximate ETA" note.
Don't demo ETAs from the fallback as if they were real.

## ETA

`services/tracking/tracking/engine.py::_update_eta` recomputes after each GPS batch: the distance still to go along the
planned route (plus the distance back onto it if the truck has strayed), divided by the route's planned average speed.
Without a stored route it uses straight-line × `FALLBACK_ROAD_FACTOR` at `FALLBACK_SPEED_KMPH`. If the ETA slips 45 min or
more past plan, a `vehicle_delay` alert fires. The ETA is pushed live on `trip:{id}`, `fleet:{org}` and `mandi:{id}`.

## Geofences

| Event | Rule |
|---|---|
| `picked_up` | driver scans the pickup QR (chain of custody, not GPS) |
| `left_pickup_zone` | first fix more than `PICKUP_RADIUS_M` (300 m) from the pickup point |
| `reached_mandi` | first fix within the mandi's `geofence_radius_m` (default 500 m, set per mandi by an admin), or the delivery QR scan if GPS never got there |
| `unexpected_stop` | no movement for 30 min away from both zones, or the phone silent for 30 min (`tracking.monitor`, every minute) |
| `delivered` | trader scans the delivery QR on the driver's phone |

Geofence circles are drawn on every trip map, so viewers can see why an event fired.

## Live pipeline

```
Driver PWA ──WebSocket /ws/driver/{trip}──┐
   (IndexedDB buffer, HTTP /points when ─┤→ process_points → gps_points (+ geofence_events, ETA)
    the socket is down; server de-dupes) ┘            │
                                                      └→ hub.publish → Redis pub/sub (REDIS_URL) → every API process
                                                             → /ws/trips/{id}, /ws/live, /ws/track/{share_token}
```

`gps_points` has primary key `(trip_id, recorded_at)`, which makes offline re-sync idempotent and already satisfies
TimescaleDB's rule for hypertables. On the TimescaleDB image, convert it once volume warrants:
`SELECT create_hypertable('gps_points', 'recorded_at', migrate_data => true);`
