"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type TripDetail } from "@/lib/api";
import { dateTime, num, time } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { MapCircle, MapLine, MapMarker } from "./MapView";
import { Card, ErrorNote, SimBadge, Status } from "./ui";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

const EVENT_LABEL: Record<string, string> = {
  picked_up: "Picked up (QR scanned)",
  left_pickup_zone: "Left pickup zone",
  reached_mandi: "Reached mandi (geofence)",
  unexpected_stop: "Unexpected stop",
  delivered: "Delivery QR scanned",
};

/** Live trip panel for signed-in users: route, breadcrumb, vehicle, geofence, ETA and event timeline. */
export default function TripLive({ tripId, compact = false }: { tripId: number; compact?: boolean }) {
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<TripDetail>(`/trips/${tripId}`).then(setTrip).catch((e) => setError(e.message));
  }, [tripId]);
  useEffect(load, [load]);

  useLive(`/ws/trips/${tripId}`, (msg) => {
    if (msg.type === "position") {
      setTrip((t) => {
        if (!t) return t;
        const lat = msg.lat as number | null;
        const lon = msg.lon as number | null;
        const track = lat != null && lon != null ? [...(t.track || []), [lon, lat] as [number, number]] : t.track;
        return { ...t, ...(msg as Partial<TripDetail>), track };
      });
    } else if (msg.type === "event" || msg.type === "status") {
      load(); // new geofence event or status change: re-read the timeline
    }
  });

  const { markers, lines, circles } = useMemo(() => {
    const markers: MapMarker[] = [];
    const lines: MapLine[] = [];
    const circles: MapCircle[] = [];
    if (!trip) return { markers, lines, circles };
    markers.push({ id: "pickup", lat: trip.origin_lat, lon: trip.origin_lon, kind: "pickup", label: "Pickup" });
    markers.push({ id: "mandi", lat: trip.mandi_lat, lon: trip.mandi_lon, kind: "mandi", label: trip.mandi });
    circles.push({ id: "mandi-zone", lat: trip.mandi_lat, lon: trip.mandi_lon, radius_m: trip.mandi_geofence_m });
    circles.push({ id: "pickup-zone", lat: trip.origin_lat, lon: trip.origin_lon, radius_m: trip.pickup_radius_m, color: "#15803d" });
    if (trip.route?.length) lines.push({ id: "route", coords: trip.route, color: "#94a3b8", dashed: trip.route_source !== "osrm", width: 4 });
    if (trip.track?.length) lines.push({ id: "track", coords: trip.track, color: "#1d4ed8", width: 4 });
    if (trip.lat != null && trip.lon != null)
      markers.push({ id: "vehicle", lat: trip.lat, lon: trip.lon, kind: "vehicle", label: trip.vehicle || "Vehicle", simulated: trip.is_simulated });
    return { markers, lines, circles };
  }, [trip]);

  if (error) return <ErrorNote error={error} />;
  if (!trip) return <p className="text-sm text-slate-500">Loading trip…</p>;

  return (
    <Card
      title={<span>Trip #{trip.id} · {trip.vehicle} → {trip.mandi} <SimBadge on={trip.is_simulated} /></span>}
      right={<Status s={trip.status} />}
    >
      <div className={`grid gap-4 ${compact ? "" : "lg:grid-cols-[2fr_1fr]"}`}>
        <MapView markers={markers} lines={lines} circles={circles} fitKey={trip.id} height={compact ? 280 : 420} />
        <div className="space-y-3 text-sm">
          <div className="rounded-lg bg-blue-50 p-3">
            {trip.status === "in_progress" ? (
              <>
                <div className="text-lg font-semibold text-blue-900">
                  {trip.remaining_km != null ? `${num(trip.remaining_km, 0)} km away` : "Waiting for first GPS fix"}
                  {trip.eta_at && <>, arriving {time(trip.eta_at)}</>}
                </div>
                <div className="text-xs text-blue-800">
                  Last GPS {dateTime(trip.last_seen_at)}{trip.speed_kmph != null && ` · ${num(trip.speed_kmph, 0)} km/h`}
                  {trip.stopped_since && <> · stopped since {time(trip.stopped_since)}</>}
                </div>
              </>
            ) : (
              <div className="font-medium text-blue-900">
                {trip.status === "completed" ? `Trip completed ${dateTime(trip.ended_at)}` : `Trip ${trip.status.replace("_", " ")}`}
              </div>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-slate-700">
            <dt className="text-slate-500">Driver</dt><dd>{trip.driver?.name || "–"}</dd>
            <dt className="text-slate-500">Load</dt><dd>{num(trip.load_tons, 2)} t</dd>
            <dt className="text-slate-500">Planned</dt>
            <dd>{num(trip.planned_distance_km, 0)} km · {num(trip.planned_duration_min, 0)} min {trip.route_source && <span className="text-xs text-slate-500">({trip.route_source})</span>}</dd>
            <dt className="text-slate-500">Tracking</dt>
            <dd>{trip.tracking_on ? <span className="font-medium text-green-700">on (driver consented)</span> : "off"}</dd>
            <dt className="text-slate-500">Pickup QR</dt><dd>{trip.pickup_scanned_at ? dateTime(trip.pickup_scanned_at) : "not yet"}</dd>
            <dt className="text-slate-500">Delivery QR</dt><dd>{trip.delivery_scanned_at ? dateTime(trip.delivery_scanned_at) : "not yet"}</dd>
          </dl>
          {trip.share_url && (
            <div className="rounded border border-slate-200 p-2 text-xs">
              Public tracking link (no login, expires):{" "}
              <a className="break-all text-green-800 underline" href={trip.share_url} target="_blank" rel="noreferrer">{trip.share_url}</a>
            </div>
          )}
          <div>
            <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Events</div>
            {trip.events?.length ? (
              <ol className="space-y-1">
                {trip.events.map((e, i) => (
                  <li key={i} className="flex justify-between gap-2">
                    <span className={e.event === "unexpected_stop" ? "text-red-700" : ""}>{EVENT_LABEL[e.event] || e.event}</span>
                    <span className="text-slate-500">{dateTime(e.at)}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-slate-500">No events yet.</p>
            )}
          </div>
        </div>
      </div>
      {trip.route_source && trip.route_source !== "osrm" && (
        <p className="mt-2 text-xs text-slate-500">Route shown as a straight-line estimate (OSRM not configured), so ETA is approximate.</p>
      )}
    </Card>
  );
}
