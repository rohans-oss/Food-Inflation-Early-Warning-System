"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import Shell from "@/components/Shell";
import TripLive from "@/components/TripLive";
import { Btn, Card, Empty, ErrorNote, Field, inputCls, SimBadge, Stat, Status, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, num, time } from "@/lib/format";
import { useLive, usePoll } from "@/lib/live";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

interface Vehicle {
  id: number;
  registration: string;
  capacity_tons: number;
  is_simulated: boolean;
  active_trip: { id: number; status: string; mandi: string; lat: number | null; lon: number | null; eta_at: string | null; load_tons: number; remaining_km: number | null } | null;
  utilization_30d: { trips: number; hours_on_trip: number; pct_of_time: number; avg_load_pct: number | null };
  history: { trip_id: number; status: string; mandi: string; started_at: string | null; ended_at: string | null; load_tons: number; km: number | null }[];
}
interface Overview {
  vehicles: Vehicle[];
  bookings_to_assign: { shipment_id: number; mandi: string; tons: number; booked_at: string }[];
}
interface Driver { id: number; name: string; phone: string | null; is_active: boolean }

export default function FleetPage() {
  return <Shell roles={["fleet_owner"]} title="Fleet owner">{() => <Fleet />}</Shell>;
}

function Fleet() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selTrip, setSelTrip] = useState<number | null>(null);
  const [newV, setNewV] = useState({ registration: "", capacity_tons: "5" });
  const [assign, setAssign] = useState<Record<number, { vehicle_id: string; driver_id: string }>>({});

  const load = useCallback(() => {
    api<Overview>("/fleet/overview").then(setOv).catch((e) => setError(e.message));
    api<Driver[]>("/drivers").then(setDrivers).catch(() => undefined);
  }, []);
  usePoll(load, 30000);

  // Live positions for the whole fleet (channel fleet:{org})
  useLive("/ws/live", (msg) => {
    if (msg.type !== "position") return;
    setOv((o) => o && {
      ...o,
      vehicles: o.vehicles.map((v) =>
        v.active_trip && v.active_trip.id === msg.trip_id
          ? { ...v, active_trip: { ...v.active_trip, lat: msg.lat as number, lon: msg.lon as number, eta_at: msg.eta_at as string, remaining_km: msg.remaining_km as number, status: msg.status as string } }
          : v),
    });
  });

  const markers = useMemo(() => (ov?.vehicles || []).filter((v) => v.active_trip?.lat != null).map((v) => ({
    id: v.id, lat: v.active_trip!.lat!, lon: v.active_trip!.lon!, kind: "vehicle" as const, label: v.registration, simulated: v.is_simulated,
  })), [ov]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); load(); } catch (e) { setError((e as Error).message); }
  };

  const active = ov?.vehicles.filter((v) => v.active_trip).length || 0;
  const util = ov?.vehicles.length ? ov.vehicles.reduce((s, v) => s + v.utilization_30d.pct_of_time, 0) / ov.vehicles.length : null;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Vehicles" value={ov?.vehicles.length ?? "–"} />
        <Stat label="On a trip now" value={active} />
        <Stat label="Avg utilisation (30 d)" value={util == null ? "–" : `${num(util, 1)}%`} sub="share of hours on a trip" />
        <Stat label="Bookings to assign" value={ov?.bookings_to_assign.length ?? "–"} />
      </div>
      <ErrorNote error={error} />

      {!!ov?.bookings_to_assign.length && (
        <Card title="Bookings from FPOs: assign a vehicle and driver">
          <Table head={["Shipment", "To", "Tons", "Booked", "Vehicle", "Driver", ""]}>
            {ov.bookings_to_assign.map((b) => {
              const a = assign[b.shipment_id] || { vehicle_id: "", driver_id: "" };
              const setA = (k: "vehicle_id" | "driver_id", v: string) => setAssign({ ...assign, [b.shipment_id]: { ...a, [k]: v } });
              return (
                <tr key={b.shipment_id}>
                  <td className="px-2 py-1.5">#{b.shipment_id}</td>
                  <td className="px-2 py-1.5">{b.mandi}</td>
                  <td className="px-2 py-1.5">{num(b.tons, 2)}</td>
                  <td className="px-2 py-1.5">{dateTime(b.booked_at)}</td>
                  <td className="px-2 py-1.5">
                    <select className="rounded border border-slate-300 px-1 py-0.5" value={a.vehicle_id} onChange={(e) => setA("vehicle_id", e.target.value)}>
                      <option value="">Vehicle…</option>
                      {ov.vehicles.filter((v) => !v.active_trip && !v.is_simulated).map((v) => <option key={v.id} value={v.id}>{v.registration} ({v.capacity_tons} t)</option>)}
                    </select>
                  </td>
                  <td className="px-2 py-1.5">
                    <select className="rounded border border-slate-300 px-1 py-0.5" value={a.driver_id} onChange={(e) => setA("driver_id", e.target.value)}>
                      <option value="">Driver…</option>
                      {drivers.filter((d) => d.is_active).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </td>
                  <td className="px-2 py-1.5">
                    <Btn disabled={!a.vehicle_id || !a.driver_id} onClick={() => act(() => api("/trips", { body: { shipment_id: b.shipment_id, vehicle_id: Number(a.vehicle_id), driver_id: Number(a.driver_id) } }))}>Assign</Btn>
                  </td>
                </tr>
              );
            })}
          </Table>
        </Card>
      )}

      <Card title="All vehicles on one map">
        <MapView markers={markers} fitKey={markers.length ? `n${markers.length}` : undefined} height={380} />
        <p className="mt-1 text-xs text-slate-500">Positions update live. Vehicles with a dashed amber ring are simulated.</p>
      </Card>

      <Card title="Vehicles, utilisation and trip history">
        {!ov?.vehicles.length ? <Empty>No vehicles yet. Add one below.</Empty> : (
          <Table head={["Vehicle", "Capacity", "Now", "Trips 30d", "Hours 30d", "Utilisation", "Avg load", "Recent trips"]}>
            {ov.vehicles.map((v) => (
              <tr key={v.id}>
                <td className="px-2 py-1.5 font-medium">{v.registration}<SimBadge on={v.is_simulated} /></td>
                <td className="px-2 py-1.5">{v.capacity_tons} t</td>
                <td className="px-2 py-1.5">
                  {v.active_trip ? (
                    <button className="text-left text-green-800 underline" onClick={() => setSelTrip(v.active_trip!.id)}>
                      → {v.active_trip.mandi}, {v.active_trip.remaining_km != null ? `${num(v.active_trip.remaining_km, 0)} km` : ""} {v.active_trip.eta_at ? `ETA ${time(v.active_trip.eta_at)}` : <Status s={v.active_trip.status} />}
                    </button>
                  ) : <span className="text-slate-500">idle</span>}
                </td>
                <td className="px-2 py-1.5">{v.utilization_30d.trips}</td>
                <td className="px-2 py-1.5">{v.utilization_30d.hours_on_trip}</td>
                <td className="px-2 py-1.5">{v.utilization_30d.pct_of_time}%</td>
                <td className="px-2 py-1.5">{v.utilization_30d.avg_load_pct == null ? "–" : `${v.utilization_30d.avg_load_pct}%`}</td>
                <td className="px-2 py-1.5 text-xs">
                  {v.history.slice(0, 3).map((h) => (
                    <button key={h.trip_id} className="mr-2 underline" onClick={() => setSelTrip(h.trip_id)}>#{h.trip_id} {h.mandi} <Status s={h.status} /></button>
                  ))}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {selTrip && <TripLive tripId={selTrip} />}

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Add a vehicle">
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
            e.preventDefault();
            act(() => api("/vehicles", { body: { registration: newV.registration, capacity_tons: Number(newV.capacity_tons) } })).then(() => setNewV({ registration: "", capacity_tons: "5" }));
          }}>
            <Field label="Registration"><input className={inputCls} placeholder="KA-05-AB-1234" value={newV.registration} onChange={(e) => setNewV({ ...newV, registration: e.target.value })} required /></Field>
            <Field label="Capacity (t)"><input className={inputCls} type="number" step="0.5" value={newV.capacity_tons} onChange={(e) => setNewV({ ...newV, capacity_tons: e.target.value })} required /></Field>
            <Btn type="submit">Add</Btn>
          </form>
        </Card>
        <Card title="Drivers">
          {!drivers.length ? <Empty>No drivers yet. Drivers sign up, choose your fleet, and appear here for approval.</Empty> : (
            <Table head={["Driver", "Phone", "Status", ""]}>
              {drivers.map((d) => (
                <tr key={d.id}>
                  <td className="px-2 py-1.5">{d.name}</td>
                  <td className="px-2 py-1.5">{d.phone || "–"}</td>
                  <td className="px-2 py-1.5">{d.is_active ? <Status s="accepted" /> : <Status s="pending" />}</td>
                  <td className="px-2 py-1.5">{!d.is_active && <Btn onClick={() => act(() => api(`/drivers/${d.id}/approve`, { method: "POST" }))}>Approve</Btn>}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
