"use client";

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { ago, dateTime, num, time } from "@/lib/format";
import { useLiveTrip } from "@/lib/live";
import { useSession } from "@/lib/session";

import { MapLine, MapMarker, MapView } from "./MapView";
import { Badge, SimBadge, Stat, StatusBadge } from "./ui";

const EVENT_LABEL: Record<string, string> = {
  picked_up: "Picked up (QR scanned)",
  left_pickup_zone: "Left pickup area",
  reached_mandi: "Reached mandi",
  unexpected_stop: "Unexpected stop",
  tracking_paused: "Driver's phone paused location (app not on screen)",
  tracking_resumed: "Location sharing resumed",
  delivered: "Delivered (QR scanned at gate)",
};

/** Live map + ETA + geofence events for one trip (any role that can read the trip). */
export function TripLive({ tripId }: { tripId: number }) {
  const { t } = useSession();
  const [trip, setTrip] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const live = useLiveTrip(tripId);

  const load = () => api(`/trips/${tripId}`).then((d) => { setTrip(d); setErr(null); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); /* eslint-disable-next-line */ }, [tripId]);
  // a geofence event arrives live -> refresh the event list
  useEffect(() => { if (live.events.length) load(); /* eslint-disable-next-line */ }, [live.events.length]);

  const cur = { ...(trip ?? {}), ...(live.pos ?? {}) };
  const [track, setTrack] = useState<[number, number][]>([]);
  useEffect(() => { if (trip?.track) setTrack(trip.track); }, [trip?.track]);
  useEffect(() => {
    if (live.pos?.lat != null) setTrack((tr) => [...tr, [live.pos.lon, live.pos.lat]]);
  }, [live.pos?.lat, live.pos?.lon]);

  const markers = useMemo<MapMarker[]>(() => {
    if (!trip) return [];
    const m: MapMarker[] = [
      { id: "o", lat: trip.origin_lat, lon: trip.origin_lon, kind: "pickup", label: "Pickup", popup: "Pickup" },
    ];
    if (trip.mandi_lat != null) m.push({ id: "m", lat: trip.mandi_lat, lon: trip.mandi_lon, kind: "mandi", label: trip.mandi, popup: trip.mandi });
    if (cur.lat != null) m.push({ id: "v", lat: cur.lat, lon: cur.lon, kind: trip.is_simulated ? "vehicle-sim" : "vehicle",
      label: trip.vehicle, popup: `${trip.vehicle}${trip.is_simulated ? " (Simulated)" : ""}` });
    return m;
  }, [trip, cur.lat, cur.lon]);

  const lines = useMemo<MapLine[]>(() => {
    const l: MapLine[] = [];
    if (trip?.route?.length) l.push({ id: "route", coords: trip.route, color: "#2a78d6", dashed: trip.route_source !== "osrm", width: 3 });
    if (track.length > 1) l.push({ id: "track", coords: track, color: "#155e35", width: 4 });
    return l;
  }, [trip?.route, trip?.route_source, track]);

  if (err) return <p className="text-sm text-critical">{err}</p>;
  if (!trip) return <p className="text-sm text-muted">{t("loading")}</p>;

  const headline = cur.remaining_km != null && cur.eta_local
    ? `Vehicle ${trip.vehicle} is ${num(cur.remaining_km, 0)} km away, arriving ${cur.eta_local}`
    : `Vehicle ${trip.vehicle}`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <b>{headline}</b>
        <StatusBadge s={cur.status} />
        <SimBadge on={trip.is_simulated} />
        {trip.route_source === "haversine" && <Badge>{t("approximateRoute")}</Badge>}
        {cur.status === "in_progress" && <Badge kind={live.connected ? "good" : "neutral"}>{live.connected ? "Live" : "Reconnecting"}</Badge>}
        {cur.status === "in_progress" && cur.tracking_paused_since && (
          <p className="w-full text-sm text-warn"><Badge kind="warn">Location paused</Badge> The driver's phone stopped sharing location at {time(cur.tracking_paused_since)} (app not on screen). The vehicle may still be moving.</p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t("eta")} value={cur.eta_local ?? "–"} />
        <Stat label={t("remaining")} value={cur.remaining_km != null ? `${num(cur.remaining_km, 0)} km` : "–"} sub={trip.planned_distance_km ? `of ${num(trip.planned_distance_km, 0)} km` : undefined} />
        <Stat label="Load" value={`${num(trip.load_tons, 1)} t`} />
        <Stat label="Last GPS fix" value={ago(cur.last_seen_at)} sub={cur.speed_kmph != null ? `${num(cur.speed_kmph, 0)} km/h` : undefined} />
      </div>
      <MapView height="h-96" markers={markers} lines={lines} fitKey={trip.id} />
      <div>
        <h3 className="mb-1 text-sm font-semibold">{t("events")}</h3>
        <ol className="space-y-1 text-sm">
          {trip.started_at && <li><span className="text-muted">{dateTime(trip.started_at)}</span> · Trip started, driver consented to location sharing</li>}
          {trip.events?.map((e: any, i: number) => (
            <li key={i}><span className="text-muted">{time(e.at)}</span> · {EVENT_LABEL[e.event] ?? e.event}
              {e.details?.minutes ? ` (${e.details.minutes} min)` : ""}{e.details?.reason === "no_signal" ? " — phone silent" : ""}{e.details?.reason === "phone_paused" ? " — phone paused, not confirmed stopped" : ""}</li>
          ))}
          {trip.ended_at && <li><span className="text-muted">{dateTime(trip.ended_at)}</span> · Trip ended, tracking off</li>}
        </ol>
      </div>
    </div>
  );
}
