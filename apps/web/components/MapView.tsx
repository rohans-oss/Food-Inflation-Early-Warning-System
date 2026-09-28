"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import type { GeoJSONSource, Map as MlMap, Marker } from "maplibre-gl";
import { useEffect, useRef } from "react";

export interface MapMarker {
  id: string | number;
  lat: number;
  lon: number;
  label?: string;
  kind?: "vehicle" | "mandi" | "pickup" | "point";
  simulated?: boolean;
  color?: string;
}

export interface MapLine {
  id: string;
  coords: [number, number][]; // [lon, lat]
  color?: string;
  dashed?: boolean;
  width?: number;
}

export interface MapCircle {
  id: string;
  lat: number;
  lon: number;
  radius_m: number;
  color?: string;
}

// OSM raster tiles. Fine for development and a small pilot; follow the OSM tile usage policy and switch to
// your own tile server or a provider before real traffic.
const STYLE = {
  version: 8 as const,
  sources: {
    osm: {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
  },
  layers: [{ id: "osm", type: "raster" as const, source: "osm" }],
};

const KIND_COLOR = { vehicle: "#1d4ed8", mandi: "#b91c1c", pickup: "#15803d", point: "#6b7280" };

function circlePolygon(lat: number, lon: number, radiusM: number): [number, number][] {
  const pts: [number, number][] = [];
  const dLat = radiusM / 111320;
  const dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * 2 * Math.PI;
    pts.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return pts;
}

export default function MapView({
  markers = [],
  lines = [],
  circles = [],
  height = 360,
  fitKey,
  center = [77.6, 12.97],
  zoom = 7,
}: {
  markers?: MapMarker[];
  lines?: MapLine[];
  circles?: MapCircle[];
  height?: number;
  /** Change this to re-fit the view to all features (e.g. the trip id). */
  fitKey?: string | number;
  center?: [number, number];
  zoom?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const loaded = useRef(false);
  const mks = useRef<Map<string, Marker>>(new Map());
  const lib = useRef<typeof import("maplibre-gl") | null>(null);
  const fitted = useRef<string | number | undefined>(undefined);
  const latest = useRef({ markers, lines, circles });
  latest.current = { markers, lines, circles };

  useEffect(() => {
    let cancelled = false;
    import("maplibre-gl").then((ml) => {
      if (cancelled || !el.current) return;
      lib.current = ml;
      const m = new ml.Map({ container: el.current, style: STYLE, center, zoom, attributionControl: { compact: true } });
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      m.on("load", () => {
        loaded.current = true;
        sync();
      });
      map.current = m;
    });
    return () => {
      cancelled = true;
      mks.current.forEach((mk) => mk.remove());
      mks.current.clear();
      map.current?.remove();
      map.current = null;
      loaded.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function sync() {
    const m = map.current;
    const ml = lib.current;
    if (!m || !ml || !loaded.current) return;
    const { markers, lines, circles } = latest.current;

    // lines + geofence circles as GeoJSON layers
    const lineFc = {
      type: "FeatureCollection" as const,
      features: lines.filter((l) => l.coords.length > 1).map((l) => ({
        type: "Feature" as const,
        properties: { color: l.color || "#2563eb", width: l.width || 4, dashed: l.dashed ? 1 : 0 },
        geometry: { type: "LineString" as const, coordinates: l.coords },
      })),
    };
    const circleFc = {
      type: "FeatureCollection" as const,
      features: circles.map((c) => ({
        type: "Feature" as const,
        properties: { color: c.color || "#b91c1c" },
        geometry: { type: "Polygon" as const, coordinates: [circlePolygon(c.lat, c.lon, c.radius_m)] },
      })),
    };
    for (const [id, data] of [["lines", lineFc], ["circles", circleFc]] as const) {
      const src = m.getSource(id) as GeoJSONSource | undefined;
      if (src) src.setData(data);
      else m.addSource(id, { type: "geojson", data });
    }
    if (!m.getLayer("circles-fill")) {
      m.addLayer({ id: "circles-fill", type: "fill", source: "circles", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.12 } });
      m.addLayer({ id: "circles-line", type: "line", source: "circles", paint: { "line-color": ["get", "color"], "line-width": 1.5 } });
      m.addLayer({
        id: "lines-solid", type: "line", source: "lines", filter: ["==", ["get", "dashed"], 0],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ["get", "color"], "line-width": ["get", "width"], "line-opacity": 0.8 },
      });
      m.addLayer({
        id: "lines-dashed", type: "line", source: "lines", filter: ["==", ["get", "dashed"], 1],
        paint: { "line-color": ["get", "color"], "line-width": ["get", "width"], "line-dasharray": [2, 2], "line-opacity": 0.7 },
      });
    }

    // DOM markers, keyed so vehicles glide instead of flickering
    const seen = new Set<string>();
    for (const mk of markers) {
      if (mk.lat == null || mk.lon == null) continue;
      const key = String(mk.id);
      seen.add(key);
      let marker = mks.current.get(key);
      if (!marker) {
        const node = document.createElement("div");
        node.className = "ap-marker";
        marker = new ml.Marker({ element: node }).setLngLat([mk.lon, mk.lat]).addTo(m);
        mks.current.set(key, marker);
      }
      const node = marker.getElement();
      const color = mk.color || KIND_COLOR[mk.kind || "point"];
      node.innerHTML = "";
      const dot = document.createElement("span");
      dot.className = `ap-dot ap-${mk.kind || "point"}`;
      dot.style.background = color;
      if (mk.simulated) dot.style.border = "2px dashed #f59e0b";
      node.appendChild(dot);
      if (mk.label) {
        const lab = document.createElement("span");
        lab.className = "ap-label";
        lab.textContent = mk.simulated ? `${mk.label} · SIMULATED` : mk.label;
        node.appendChild(lab);
      }
      marker.setLngLat([mk.lon, mk.lat]);
    }
    for (const [key, marker] of mks.current) {
      if (!seen.has(key)) {
        marker.remove();
        mks.current.delete(key);
      }
    }

    if (fitKey !== undefined && fitted.current !== fitKey) {
      const pts: [number, number][] = [
        ...markers.filter((x) => x.lat != null && x.lon != null).map((x) => [x.lon, x.lat] as [number, number]),
        ...lines.flatMap((l) => l.coords),
      ];
      if (pts.length) {
        const b = new ml.LngLatBounds(pts[0], pts[0]);
        pts.forEach((p) => b.extend(p));
        m.fitBounds(b, { padding: 50, maxZoom: 13, duration: 0 });
        fitted.current = fitKey;
      }
    }
  }

  useEffect(() => {
    sync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markers, lines, circles, fitKey]);

  return <div ref={el} style={{ height }} className="w-full overflow-hidden rounded-lg border border-slate-200" />;
}
