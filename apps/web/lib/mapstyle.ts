/** V3-3 (backlog 7): where map tiles come from. Set ONE at build time (NEXT_PUBLIC_* are compiled in):
 *   NEXT_PUBLIC_MAP_PMTILES   self-hosted: a PMTiles file (e.g. /tiles/india.pmtiles) + fonts/sprites under
 *                             /tiles/assets, drawn with the Protomaps basemap style, labels in the user's language
 *   NEXT_PUBLIC_MAP_STYLE_URL any MapLibre style.json (a tile provider or your own server)
 *   NEXT_PUBLIC_MAP_TILES     a raster tile URL template ({z}/{x}/{y}) + NEXT_PUBLIC_MAP_ATTRIBUTION
 * Nothing set: the public OpenStreetMap tile server, which is fine for development and demos but its usage policy
 * does not allow production traffic (docs/map-tiles.md). */
import type { StyleSpecification } from "maplibre-gl";

const PMTILES = process.env.NEXT_PUBLIC_MAP_PMTILES || "";
const STYLE_URL = process.env.NEXT_PUBLIC_MAP_STYLE_URL || "";
const TILES = process.env.NEXT_PUBLIC_MAP_TILES || "";
const ATTRIBUTION = process.env.NEXT_PUBLIC_MAP_ATTRIBUTION || "";

export const MAP_SOURCE = PMTILES ? "self-hosted (PMTiles)" : STYLE_URL ? "style URL" : TILES ? "raster tiles" : "public OSM (development only)";
export const USING_PUBLIC_OSM = !PMTILES && !STYLE_URL && !TILES;

function raster(url: string, attribution: string): StyleSpecification {
  return {
    version: 8,
    sources: { base: { type: "raster", tiles: [url], tileSize: 256, maxzoom: 19, attribution } },
    layers: [{ id: "base", type: "raster", source: "base" }],
  };
}

let protocolAdded = false;

export async function mapStyle(ml: typeof import("maplibre-gl"), lang: string): Promise<StyleSpecification | string> {
  if (PMTILES) {
    const [{ Protocol }, basemaps] = await Promise.all([import("pmtiles"), import("@protomaps/basemaps")]);
    if (!protocolAdded) {
      ml.addProtocol("pmtiles", new Protocol().tile);
      protocolAdded = true;
    }
    const abs = new URL(PMTILES, window.location.href).href;
    const assets = new URL(PMTILES, window.location.href).href.replace(/\/[^/]*$/, "/assets");
    return {
      version: 8,
      glyphs: `${assets}/fonts/{fontstack}/{range}.pbf`,
      sprite: `${assets}/sprites/v4/light`,
      sources: {
        protomaps: { type: "vector", url: `pmtiles://${abs}`,
          attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OpenStreetMap</a>' },
      },
      layers: basemaps.layers("protomaps", basemaps.namedFlavor("light"), { lang: ["en", "kn", "hi"].includes(lang) ? lang : "en" }),
    } as StyleSpecification;
  }
  if (STYLE_URL) return STYLE_URL;
  if (TILES) return raster(TILES, ATTRIBUTION || "© OpenStreetMap contributors");
  return raster("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors");
}
