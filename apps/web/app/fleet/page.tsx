"use client";

import { useMemo, useState } from "react";

import { ReturnLoadsCard } from "@/components/LoadProposals";
import { MapMarker, MapView } from "@/components/MapView";
import { Shell } from "@/components/Shell";
import { TripLive } from "@/components/TripLive";
import { Button, Card, ErrorNote, Field, inputCls, SimBadge, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, inr, num, time, tons } from "@/lib/format";
import { useLiveFeed } from "@/lib/live";
import { useSession } from "@/lib/session";

export default function Fleet() {
  const { t } = useSession();
  const ov = useApi<any>("/fleet/overview", { poll: 30000 });
  const drivers = useApi<any[]>("/drivers");
  const [live, setLive] = useState<Record<number, any>>({}); // trip_id -> latest position
  const [assign, setAssign] = useState<Record<number, { vehicle: string; driver: string }>>({});
  const [nv, setNv] = useState({ registration: "", capacity_tons: "5" });
  const [watch, setWatch] = useState<number | null>(null);
  const act = useAction();

  useLiveFeed((m) => { if (m.type === "position" && m.trip_id) setLive((s) => ({ ...s, [m.trip_id]: m })); });

  const vehicles: any[] = ov.data?.vehicles ?? [];
  const markers = useMemo<MapMarker[]>(() => vehicles.filter((v) => v.active_trip).map((v) => {
    const p = live[v.active_trip.id] ?? v.active_trip;
    return p.lat == null ? null : {
      id: v.id, lat: p.lat, lon: p.lon, kind: v.is_simulated ? "vehicle-sim" : "vehicle", label: v.registration,
      popup: `${v.registration}${v.is_simulated ? " (Simulated)" : ""} → ${v.active_trip.mandi}, ETA ${time(p.eta_at)}`,
    } as MapMarker;
  }).filter(Boolean) as MapMarker[], [vehicles, live]);

  const doAssign = (sid: number) => act.run(async () => {
    const a = assign[sid];
    await api("/trips", { method: "POST", body: { shipment_id: sid, vehicle_id: Number(a.vehicle), driver_id: Number(a.driver) } });
    ov.reload();
  });
  const addVehicle = (e: React.FormEvent) => { e.preventDefault(); act.run(async () => {
    await api("/vehicles", { method: "POST", body: { registration: nv.registration, capacity_tons: Number(nv.capacity_tons) } });
    setNv({ registration: "", capacity_tons: "5" });
    ov.reload();
  }); };
  const approve = (id: number) => act.run(async () => { await api(`/drivers/${id}/approve`, { method: "POST" }); drivers.reload(); });

  const activeDrivers = (drivers.data ?? []).filter((d) => d.is_active);
  const freeVehicles = vehicles.filter((v) => !v.active_trip && !v.is_simulated);

  return (
    <Shell roles={["fleet_owner"]} title="Fleet" wide>
      <ErrorNote error={act.error ?? ov.error} />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card title="Vehicles on the road">
          <MapView height="h-[28rem]" markers={markers} fitKey={markers.length} />
          <p className="mt-2 text-xs text-muted">Dashed amber markers are simulated vehicles.</p>
        </Card>
        <Card title="Bookings to assign">
          {ov.data?.bookings_to_assign?.length === 0 && <p className="text-sm text-muted">No open bookings.</p>}
          <div className="space-y-3">
            {ov.data?.bookings_to_assign?.map((b: any) => (
              <div key={b.shipment_id} className="space-y-2 rounded-xl border border-line p-3 text-sm">
                <div>Shipment #{b.shipment_id} → <b>{b.mandi}</b> · {tons(b.tons)}</div>
                {b.farmer_booking && (
                  <div className="rounded-lg bg-page px-3 py-2 text-xs">
                    <b>Farmer booking</b> · {b.farmer_booking.farmer} · pickup <b>{b.farmer_booking.pickup_local}</b>
                    {b.farmer_booking.pickup_label ? ` at ${b.farmer_booking.pickup_label}` : ""} · fare (est.) {inr(b.farmer_booking.fare_estimate)}
                    <button className="ml-2 text-critical underline" disabled={act.busy}
                      onClick={() => act.run(async () => { await api(`/bookings/${b.farmer_booking.id}/decline`, { method: "POST", body: { reason: "No truck free at that time" } }); ov.reload(); })}>
                      Decline
                    </button>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <select aria-label="Vehicle" className={inputCls} value={assign[b.shipment_id]?.vehicle ?? ""}
                    onChange={(e) => setAssign({ ...assign, [b.shipment_id]: { ...(assign[b.shipment_id] ?? { driver: "" }), vehicle: e.target.value } })}>
                    <option value="">Vehicle…</option>
                    {freeVehicles.map((v) => <option key={v.id} value={v.id} disabled={v.capacity_tons < b.tons}>{v.registration} ({v.capacity_tons} t)</option>)}
                  </select>
                  <select aria-label="Driver" className={inputCls} value={assign[b.shipment_id]?.driver ?? ""}
                    onChange={(e) => setAssign({ ...assign, [b.shipment_id]: { ...(assign[b.shipment_id] ?? { vehicle: "" }), driver: e.target.value } })}>
                    <option value="">Driver…</option>
                    {activeDrivers.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                </div>
                <Button disabled={!assign[b.shipment_id]?.vehicle || !assign[b.shipment_id]?.driver || act.busy} onClick={() => doAssign(b.shipment_id)}>{t("assign")}</Button>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <ReturnLoadsCard onChanged={ov.reload} />

      <Card title={`${t("vehicles")} · ${t("utilization")}`}>
        <Table head={["Vehicle", "Capacity", "Now", "Trips", "Hours on trip", "% of time", "Avg load", ""]}>
          {vehicles.map((v) => (
            <tr key={v.id}>
              <Td>{v.registration} <SimBadge on={v.is_simulated} /></Td>
              <Td>{tons(v.capacity_tons)}</Td>
              <Td>{v.active_trip ? <>→ {v.active_trip.mandi} <StatusBadge s={v.active_trip.status} /></> : <span className="text-muted">idle</span>}</Td>
              <Td>{v.utilization_30d.trips}</Td>
              <Td>{num(v.utilization_30d.hours_on_trip, 1)}</Td>
              <Td>{num(v.utilization_30d.pct_of_time, 1)}%</Td>
              <Td>{v.utilization_30d.avg_load_pct != null ? `${num(v.utilization_30d.avg_load_pct, 0)}%` : "–"}</Td>
              <Td>{v.active_trip && <button className="underline" onClick={() => setWatch(v.active_trip.id)}>Watch</button>}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {watch && <Card title={`Trip #${watch}`} action={<button className="text-sm underline" onClick={() => setWatch(null)}>Close</button>}><TripLive tripId={watch} /></Card>}

      <Card title="Trip history">
        <Table head={["Trip", "Vehicle", "Mandi", "Started", "Ended", "Load", "km", "Status"]}>
          {vehicles.flatMap((v) => v.history.map((h: any) => ({ ...h, reg: v.registration, sim: v.is_simulated })))
            .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? "")).slice(0, 30)
            .map((h) => (
              <tr key={h.trip_id}>
                <Td>#{h.trip_id} <SimBadge on={h.sim} /></Td><Td>{h.reg}</Td><Td>{h.mandi}</Td><Td>{dateTime(h.started_at)}</Td>
                <Td>{dateTime(h.ended_at)}</Td><Td>{tons(h.load_tons)}</Td><Td>{num(h.km, 0)}</Td><Td><StatusBadge s={h.status} /></Td>
              </tr>
            ))}
        </Table>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title={t("addVehicle")}>
          <form onSubmit={addVehicle} className="flex flex-wrap items-end gap-3">
            <Field label="Registration"><input className={inputCls} required placeholder="KA-05-AB-1234" value={nv.registration} onChange={(e) => setNv({ ...nv, registration: e.target.value })} /></Field>
            <Field label="Capacity (t)"><input className={inputCls} type="number" min="0.5" max="60" step="0.5" value={nv.capacity_tons} onChange={(e) => setNv({ ...nv, capacity_tons: e.target.value })} /></Field>
            <Button type="submit" disabled={act.busy}>Add</Button>
          </form>
        </Card>
        <Card title={t("drivers")}>
          <p className="mb-2 text-xs text-muted">Drivers register themselves and pick your fleet; approve them here.</p>
          <Table head={["Name", "Phone", "Status", ""]}>
            {drivers.data?.map((d) => (
              <tr key={d.id}>
                <Td>{d.name}</Td><Td>{d.phone ?? "–"}</Td>
                <Td>{d.is_active ? "active" : "awaiting approval"}</Td>
                <Td>{!d.is_active && <Button variant="secondary" onClick={() => approve(d.id)}>Approve</Button>}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </Shell>
  );
}
