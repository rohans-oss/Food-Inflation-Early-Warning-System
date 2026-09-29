# Routing and ETA (OSRM)

The API asks OSRM for a road route when a trip is assigned (`GET /route/v1/driving/{lon},{lat};{lon},{lat}?overview=full&geometries=geojson`)
and stores the geometry, distance and duration on the trip. Live ETA = remaining distance along that route ÷ the
route's average speed, from the latest GPS fix.

Without OSRM (`OSRM_URL` empty or unreachable) it falls back to straight-line distance × 1.3 at 40 km/h. Every such
trip is marked `route_source = "haversine"` and the UI shows **Approximate route**.

## Set up (one time, ~20–40 min, ~8 GB RAM for the southern zone)

```bash
bash infra/osrm/prepare.sh                 # downloads the Geofabrik southern-zone extract, builds MLD files
docker compose --profile routing up -d osrm
echo "OSRM_URL=http://osrm:5000" >> .env && docker compose up -d api
curl "http://localhost:5000/route/v1/driving/78.02,13.20;78.129,13.137?overview=false"
```

Check the extract name at https://download.geofabrik.de/asia/india.html before running. Geofabrik splits India
into zones, and the southern zone covers Karnataka, Tamil Nadu, Andhra Pradesh, Telangana and Kerala. For
Maharashtra mandis add the western zone, or use the full India extract (needs far more RAM).

The public OSRM demo server is not for application use; don't point `OSRM_URL` at it.
