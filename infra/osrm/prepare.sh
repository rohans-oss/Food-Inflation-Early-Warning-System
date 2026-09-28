#!/usr/bin/env bash
# One-time OSRM preparation for South India (Karnataka + neighbours).
# Geofabrik splits India into zones; the southern zone covers KA, TN, AP, TS, KL.
# Check the exact file name at https://download.geofabrik.de/asia/india.html before running.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data
PBF_URL="${PBF_URL:-https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf}"
IMG=ghcr.io/project-osrm/osrm-backend:v5.27.1

[ -f data/region.osm.pbf ] || curl -L --fail -o data/region.osm.pbf "$PBF_URL"
docker run --rm -t -v "$PWD/data:/data" $IMG osrm-extract -p /opt/car.lua /data/region.osm.pbf
docker run --rm -t -v "$PWD/data:/data" $IMG osrm-partition /data/region.osrm
docker run --rm -t -v "$PWD/data:/data" $IMG osrm-customize /data/region.osrm
echo "Done. Start with: docker compose --profile routing up -d osrm  and set OSRM_URL=http://osrm:5000"
