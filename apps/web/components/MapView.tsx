"use client";

import type { Map as MLMap, Marker } from "maplibre-gl";
import { useEffect, useRef } from "react";

export type MarkerKind = "vehicle" | "vehicle-sim" | "mandi" | "pickup" | "status";

export interface MapMarker {
  id: string | number;
  lat: number;
  lon: number;
  kind: MarkerKind;
  label?: string;
  popup?: string;
  color?: string; // for "status" markers: a status colour (paired with a label in the popup and the table)
  size?: number; // px, "status" markers only
}
export interface MapLine {
  id: string;
  coords: [number, number][]; // [lon, lat]
  color: string;
  dashed?: boolean;
  width?: number;
}

// OSM's public tiles are fine for development and a demo; for a pilot, point this at your own tile server.
const STYLE = {
  version: 8 as const,
  sources: {
    osm: {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster" as const, source: "osm" }],
};

function markerEl(m: MapMarker): HTMLElement {
  const el = document.createElement("div");
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", m.label ?? m.kind);
  el.style.cursor = m.popup ? "pointer" : "default";
  const base = "display:grid;place-items:center;font:600 11px system-ui;box-shadow:0 0 0 2px #fff,0 1px 4px rgba(0,0,0,.35);";
  switch (m.kind) {
    case "vehicle":
    case "vehicle-sim": {
      const sim = m.kind === "vehicle-sim";
      el.style.cssText = base + `width:30px;height:30px;border-radius:8px;background:${sim ? "#fef3c7" : "#155e35"};` +
        `color:${sim ? "#92400e" : "#fff"};${sim ? "outline:2px dashed #b45309;outline-offset:1px;" : ""}`;
      el.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M2 6h12v9H2zM14 9h4l3 3.5V15h-7z"/><circle cx="6" cy="17" r="2"/><circle cx="17" cy="17" r="2"/></svg>`;
      break;
    }
    case "mandi":
      el.style.cssText = base + "width:16px;height:16px;border-radius:4px;background:#0b0b0b;";
      break;
    case "pickup":
      el.style.cssText = base + "width:18px;height:18px;border-radius:50%;background:#2a78d6;";
      break;
    case "status": {
      const s = m.size ?? 16;
      el.style.cssText = `width:${s}px;height:${s}px;border-radius:50%;background:${m.color ?? "#898781"};` +
        "opacity:.85;box-shadow:0 0 0 2px #fff;";
      break;
    }
  }
  return el;
}

export function MapView({
  markers = [],
  lines = [],
  onClick,
  fitKey,
  height = "h-80",
  center = [77.6, 13.0],
  zoom = 7,
}: {
  markers?: MapMarker[];
  lines?: MapLine[];
  onClick?: (lat: number, lon: number) => void;
  /** change this value to re-fit the view to the current markers + lines */
  fitKey?: string | number;
  height?: string;
  center?: [number, number];
  zoom?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const lib = useRef<typeof import("maplibre-gl") | null>(null);
  const mk = useRef<Marker[]>([]);
  const loaded = useRef(false);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;
  const latest = useRef({ markers, lines });
  latest.current = { markers, lines };

  // create once
  useEffect(() => {
    let cancelled = false;
    import("maplibre-gl").then((ml) => {
      if (cancelled || !box.current) return;
      lib.current = ml;
      const m = new ml.Map({ container: box.current, style: STYLE, center, zoom, attributionControl: { compact: true } });
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      m.on("click", (e) => clickRef.current?.(e.lngLat.lat, e.lngLat.lng));
      m.on("load", () => {
        loaded.current = true;
        draw();
        fit();
      });
      map.current = m;
    });
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
      loaded.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function draw() {
    const m = map.current, ml = lib.current;
    if (!m || !ml || !loaded.current) return;
    const { markers, lines } = latest.current;
    mk.current.forEach((x) => x.remove());
    mk.current = markers.map((d) => {
      const marker = new ml.Marker({ element: markerEl(d) }).setLngLat([d.lon, d.lat]);
      if (d.popup) marker.setPopup(new ml.Popup({ offset: 14, closeButton: false }).setText(d.popup));
      return marker.addTo(m);
    });
    // lines: one source per line id
    const want = new Set(lines.map((l) => `line-${l.id}`));
    for (const layer of m.getStyle().layers ?? []) {
      if (layer.id.startsWith("line-") && !want.has(layer.id)) {
        m.removeLayer(layer.id);
        m.removeSource(layer.id);
      }
    }
    for (const l of lines) {
      const id = `line-${l.id}`;
      const data = { type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: l.coords } };
      const src = m.getSource(id) as import("maplibre-gl").GeoJSONSource | undefined;
      if (src) src.setData(data);
      else {
        m.addSource(id, { type: "geojson", data });
        m.addLayer({
          id, type: "line", source: id,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": l.color, "line-width": l.width ?? 3, ...(l.dashed ? { "line-dasharray": [2, 2] } : {}) },
        });
      }
    }
  }

  function fit() {
    const m = map.current, ml = lib.current;
    if (!m || !ml || !loaded.current) return;
    const { markers, lines } = latest.current;
    const pts: [number, number][] = [...markers.map((d) => [d.lon, d.lat] as [number, number]), ...lines.flatMap((l) => l.coords)];
    if (!pts.length) return;
    const b = new ml.LngLatBounds(pts[0], pts[0]);
    pts.forEach((p) => b.extend(p));
    m.fitBounds(b, { padding: 48, maxZoom: 13, duration: 0 });
  }

  useEffect(draw, [markers, lines]);
  useEffect(fit, [fitKey]);

  return <div ref={box} className={`w-full overflow-hidden rounded-xl border border-line ${height}`} />;
}
