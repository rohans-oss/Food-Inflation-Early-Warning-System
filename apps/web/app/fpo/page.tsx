"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import TripLive from "@/components/TripLive";
import { Btn, Card, Empty, ErrorNote, inputCls, QR, SimBadge, Stat, Status, Table } from "@/components/ui";
import { api, type Lot, type Mandi } from "@/lib/api";
import { dateTime, num, rs } from "@/lib/format";
import { usePoll } from "@/lib/live";

interface Shipment {
  id: number;
  mandi_id: number;
  mandi: string;
  status: string;
  fleet: { id: number; name: string } | null;
  booked_at: string | null;
  total_tons: number;
  farmers: { farmer_id: number; farmer: string; tons: number; lots: number[]; delivered_kg: number; payout: string[] }[];
  lots: { id: number; farmer: string; tons: number; status: string; delivered_weight_kg: number | null; sale_price_per_quintal: number | null; payout_status: string }[];
  trip: { id: number; status: string; vehicle: string; pickup_qr_token: string | null; eta_at: string | null; is_simulated: boolean } | null;
  is_simulated: boolean;
  created_at: string;
}

export default function FpoPage() {
  return <Shell roles={["fpo"]} title="FPO / aggregator">{() => <Fpo />}</Shell>;
}

function Fpo() {
  const [lots, setLots] = useState<Lot[]>([]);
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [mandis, setMandis] = useState<Mandi[]>([]);
  const [fleets, setFleets] = useState<{ id: number; name: string }[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [mandiId, setMandiId] = useState("");
  const [selShip, setSelShip] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Lot[]>("/lots").then(setLots).catch((e) => setError(e.message));
    api<Shipment[]>("/shipments").then(setShipments).catch((e) => setError(e.message));
  }, []);
  usePoll(load, 20000);
  useEffect(() => {
    api<Mandi[]>("/mandis").then((m) => setMandis(m.filter((x) => x.lat != null))).catch(() => undefined);
    api<{ id: number; name: string }[]>("/orgs/directory?kind=fleet").then(setFleets).catch(() => undefined);
  }, []);

  const open = lots.filter((l) => l.status === "registered" && !l.shipment_id);
  const pickedTons = open.filter((l) => picked.has(l.id)).reduce((s, l) => s + l.quantity_tons, 0);
  const farmers = new Set(lots.map((l) => l.farmer.id)).size;
  const unpaid = lots.filter((l) => l.status === "delivered" && l.payout_status !== "paid");

  async function group() {
    setError(null);
    try {
      const sh = await api<Shipment>("/shipments", { body: { mandi_id: Number(mandiId), lot_ids: [...picked] } });
      setPicked(new Set());
      setSelShip(sh.id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); load(); } catch (e) { setError((e as Error).message); }
  };

  const ship = shipments.find((s) => s.id === selShip);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Member farmers with lots" value={farmers} />
        <Stat label="Tons waiting to ship" value={num(open.reduce((s, l) => s + l.quantity_tons, 0), 1)} />
        <Stat label="Shipments in transit" value={shipments.filter((s) => s.status === "in_transit").length} />
        <Stat label="Payouts due" value={unpaid.length} sub={unpaid.length ? "delivered, not yet paid" : undefined} />
      </div>
      <ErrorNote error={error} />

      <Card title="1 · Group farmer lots into one shipment">
        {!open.length ? (
          <Empty>No ungrouped lots. Farmers who pick your FPO when registering a lot show up here.</Empty>
        ) : (
          <>
            <Table head={["", "Lot", "Farmer", "Tons", "Grade", "Pickup", "Registered"]}>
              {open.map((l) => (
                <tr key={l.id}>
                  <td className="px-2 py-1.5">
                    <input type="checkbox" checked={picked.has(l.id)} onChange={(e) => {
                      const n = new Set(picked);
                      if (e.target.checked) n.add(l.id); else n.delete(l.id);
                      setPicked(n);
                    }} />
                  </td>
                  <td className="px-2 py-1.5">#{l.id}<SimBadge on={l.is_simulated} /></td>
                  <td className="px-2 py-1.5">{l.farmer.name}</td>
                  <td className="px-2 py-1.5">{num(l.quantity_tons, 2)}</td>
                  <td className="px-2 py-1.5">{l.grade}</td>
                  <td className="px-2 py-1.5">{l.pickup_label || `${l.pickup_lat.toFixed(3)}, ${l.pickup_lon.toFixed(3)}`}</td>
                  <td className="px-2 py-1.5 text-slate-500">{dateTime(l.created_at)}</td>
                </tr>
              ))}
            </Table>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-sm">{picked.size} lots · {num(pickedTons, 2)} t → </span>
              <select className={`${inputCls} max-w-xs`} value={mandiId} onChange={(e) => setMandiId(e.target.value)}>
                <option value="">Choose destination mandi…</option>
                {mandis.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.district})</option>)}
              </select>
              <Btn disabled={!picked.size || !mandiId} onClick={group}>Create shipment</Btn>
            </div>
            <p className="mt-1 text-xs text-slate-500">Tip: each farmer sees a best-mandi ranking for their own lot. V1 routes one truck from the tonnage-weighted centre of the lots (multi-stop pickup is V3).</p>
          </>
        )}
      </Card>

      <Card title="2 · Shipments: book a vehicle, follow the trip, settle payouts">
        {!shipments.length ? (
          <Empty>No shipments yet.</Empty>
        ) : (
          <Table head={["Shipment", "Mandi", "Tons", "Farmers", "Status", "Fleet", "Vehicle", ""]}>
            {shipments.map((s) => (
              <tr key={s.id} className={s.id === selShip ? "bg-green-50" : ""}>
                <td className="px-2 py-1.5">#{s.id}<SimBadge on={s.is_simulated} /></td>
                <td className="px-2 py-1.5">{s.mandi}</td>
                <td className="px-2 py-1.5">{num(s.total_tons, 2)}</td>
                <td className="px-2 py-1.5">{s.farmers.length}</td>
                <td className="px-2 py-1.5"><Status s={s.status} /></td>
                <td className="px-2 py-1.5">
                  {s.fleet ? s.fleet.name : (
                    <select className="rounded border border-slate-300 px-1 py-0.5 text-sm" defaultValue="" onChange={(e) => e.target.value && act(() => api(`/shipments/${s.id}/book`, { body: { fleet_org_id: Number(e.target.value) } }))}>
                      <option value="">Book a fleet…</option>
                      {fleets.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                  )}
                </td>
                <td className="px-2 py-1.5">{s.trip ? <>{s.trip.vehicle}<SimBadge on={s.trip.is_simulated} /></> : s.status === "booked" ? <span className="text-xs text-slate-500">fleet assigning…</span> : "–"}</td>
                <td className="px-2 py-1.5"><Btn variant="secondary" onClick={() => setSelShip(s.id)}>Open</Btn></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {ship && (
        <>
          <Card title={`Shipment #${ship.id}: farmer-wise tonnage and payout`}>
            <Table head={["Farmer", "Declared t", "Weighed kg", "Lots", "Value", "Payout"]}>
              {ship.lots.map((l) => (
                <tr key={l.id}>
                  <td className="px-2 py-1.5">{l.farmer}</td>
                  <td className="px-2 py-1.5">{num(l.tons, 2)}</td>
                  <td className="px-2 py-1.5">{num(l.delivered_weight_kg, 0)}</td>
                  <td className="px-2 py-1.5">#{l.id} <Status s={l.status} /></td>
                  <td className="px-2 py-1.5">{l.delivered_weight_kg && l.sale_price_per_quintal ? rs((l.delivered_weight_kg / 100) * l.sale_price_per_quintal) : "–"}</td>
                  <td className="px-2 py-1.5">
                    {l.status === "delivered" && l.payout_status !== "paid" ? (
                      <Btn variant="secondary" onClick={() => act(() => api(`/lots/${l.id}/payout`, { method: "POST" }))}>Mark paid</Btn>
                    ) : <Status s={l.status === "delivered" ? l.payout_status : null} />}
                  </td>
                </tr>
              ))}
            </Table>
          </Card>
          {ship.trip?.pickup_qr_token && (
            <Card title="Pickup QR: the driver scans this at loading">
              <QR value={ship.trip.pickup_qr_token} />
            </Card>
          )}
          {ship.trip && <TripLive tripId={ship.trip.id} />}
        </>
      )}
    </div>
  );
}
