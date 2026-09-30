# Map tiles (V3-3, backlog 7)

Every map (farmer lot page, fleet map, policy map, trip view, mandi locations) takes its tiles from
`apps/web/lib/mapstyle.ts`. **By default it uses the public OpenStreetMap tile server.** That is fine for
development and a demo, but **OSM's tile usage policy does not allow production traffic.** The Admin page says so
while the default is in use.

Set **one** of these as a build argument. `NEXT_PUBLIC_*` values are compiled into the web bundle, so rebuild `web`
after changing them.

| Option | Setting | Notes |
|---|---|---|
| **Self-hosted (recommended for a pilot)** | `NEXT_PUBLIC_MAP_PMTILES=/tiles/karnataka.pmtiles` | One static file served by Caddy (range requests), drawn with the Protomaps light basemap. Labels come out in the user's language (English, Kannada, Hindi) where OSM has names. No tile server process. |
| A tile provider | `NEXT_PUBLIC_MAP_STYLE_URL=https://…/style.json?key=…` | Any MapLibre style (MapTiler, Stadia, your own). Check the provider's terms and keep the key domain-restricted. |
| Raster tiles | `NEXT_PUBLIC_MAP_TILES=https://…/{z}/{x}/{y}.png` + `NEXT_PUBLIC_MAP_ATTRIBUTION=…` | Any XYZ raster server you are allowed to use. |

## Self-hosting with PMTiles

On the server, in the repo directory:

1. **Install the `pmtiles` CLI** (a single binary): <https://github.com/protomaps/go-pmtiles/releases>.
2. **Cut a regional extract** from a Protomaps daily planet build. Pick a date listed at
   <https://maps.protomaps.com/builds/>.

   ```
   # Karnataka + neighbouring states (a few hundred MB)
   pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles infra/tiles/karnataka.pmtiles --bbox=72.5,8.0,84.5,19.9
   # or all of India (larger)
   pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles infra/tiles/india.pmtiles --bbox=68.1,6.5,97.4,35.7
   ```
3. **Fonts and sprites.** Download the ZIPs from <https://github.com/protomaps/basemaps-assets> and unpack them so
   the layout is:

   ```
   infra/tiles/assets/fonts/{fontstack}/{range}.pbf
   infra/tiles/assets/sprites/v4/light.json|.png|@2x.json|@2x.png
   ```
4. Set `NEXT_PUBLIC_MAP_PMTILES=/tiles/karnataka.pmtiles` in `.env` and rebuild:

   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build web caddy
   ```

   Caddy serves `infra/tiles/` at `/tiles/` (see `infra/Caddyfile`). The directory is mounted read-only and the
   large files are gitignored.

**Attribution:** the map shows "Protomaps © OpenStreetMap" automatically. OSM data is ODbL.

**Refresh:** re-run step 2 every few months to pick up new roads. Nothing else changes.

## Why not keep the public tiles?

The OSM Foundation's tile servers are run on donations for editing and light use. Heavy or commercial use can be
blocked without notice, and a blocked map would break the farmer and fleet screens during a pilot.
