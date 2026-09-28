"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import PricePanel from "@/components/PricePanel";
import Scanner from "@/components/Scanner";
import Shell from "@/components/Shell";
import { Btn, Card, Empty, ErrorNote, inputCls, SimBadge, Stat, Table } from "@/components/ui";
import { api, type User } from "@/lib/api";
import { num, rs, time } from "@/lib/format";
import { useLive, usePoll } from "@/lib/live";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

interface Board {
  mandi: { id: number; name: string };
  summary: {
    tons_in_transit: number; tons_real: number; tons_simulated: number; expected_today_tons_total: number; confirmed_today_tons: number;
    typical_daily_tons: number | null; typical_is_synthetic: boolean; expected_vs_normal: number | null; note: string;
  };
  incoming: { trip_id: number; vehicle: string; tons: number; status: string; eta_at: string | null; remaining_km: number | null; is_simulated: boolean; lat: number | null; lon: number | null; pickup_scanned: boolean }[];
  awaiting_weighing: { lot_id: number; farmer: string; declared_tons: number; grade: string; shipment_id: number }[];
  delivered_last_24h: { lot_id: number; farmer: string; kg: number; price_per_quintal: number }[];
}

export default function TraderPage() {
  return <Shell roles={["trader"]} title="Mandi trader / commission agent">{(u) => <Trader user={u} />}</Shell>;
}

function Trader({ user }: { user: User }) {
  const [b, setB] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanTrip, setScanTrip] = useState<number | null>(null);
  const [weigh, setWeigh] = useState<Record<number, { kg: string; price: string }>>({});
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => api<Board>("/trader/board").then(setB).catch((e) => setError(e.message)), []);
  usePoll(load, 30000);
  useLive("/ws/live", (m) => {
    if (m.type !== "position") return;
    setB((x) => x && { ...x, incoming: x.incoming.map((i) => i.trip_id === m.trip_id ? { ...i, lat: m.lat as number, lon: m.lon as number, eta_at: m.eta_at as string, remaining_km: m.remaining_km as number } : i) });
  });

  const markers = useMemo(() => (b?.incoming || []).filter((i) => i.lat != null).map((i) => ({
    id: i.trip_id, lat: i.lat!, lon: i.lon!, kind: "vehicle" as const, label: `${i.vehicle} · ${num(i.tons, 1)} t`, simulated: i.is_simulated,
  })), [b]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setMsg(null);
    try { await fn(); setMsg(ok); load(); } catch (e) { setError((e as Error).message); }
  };

  if (!b) return <><ErrorNote error={error} /><Empty>Loading board…</Empty></>;
  const s = b.summary;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium text-slate-700">{b.mandi.name}</h2>
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Tons in transit" value={num(s.tons_in_transit, 1)} sub={`${num(s.tons_real, 1)} real · ${num(s.tons_simulated, 1)} simulated`} />
        <Stat label="Expected today" value={`${num(s.expected_today_tons_total, 1)} t`} sub={`confirmed ${num(s.confirmed_today_tons, 1)} t so far`} />
        <Stat label="Typical day" value={s.typical_daily_tons == null ? "–" : `${num(s.typical_daily_tons, 1)} t`} sub={s.typical_is_synthetic ? "from synthetic history" : "28-day median"} />
        <Stat label="Today vs normal" value={s.expected_vs_normal == null ? "–" : `${num(s.expected_vs_normal * 100, 0)}%`} sub="tracked supply only" />
      </div>
      <p className="text-xs text-slate-500">{s.note}</p>
      <ErrorNote error={error} />
      {msg && <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">{msg}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Incoming supply">
          {!b.incoming.length ? <Empty>No tracked trucks heading here right now.</Empty> : (
            <Table head={["Vehicle", "Tons", "Distance", "ETA", "Pickup", ""]}>
              {b.incoming.map((i) => (
                <tr key={i.trip_id}>
                  <td className="px-2 py-1.5">{i.vehicle}<SimBadge on={i.is_simulated} /></td>
                  <td className="px-2 py-1.5">{num(i.tons, 2)}</td>
                  <td className="px-2 py-1.5">{i.remaining_km == null ? "–" : `${num(i.remaining_km, 0)} km`}</td>
                  <td className="px-2 py-1.5">{time(i.eta_at)}</td>
                  <td className="px-2 py-1.5">{i.pickup_scanned ? "✓ scanned" : "–"}</td>
                  <td className="px-2 py-1.5">{i.status === "in_progress" && !i.is_simulated && <Btn variant="secondary" onClick={() => setScanTrip(i.trip_id)}>Confirm arrival</Btn>}</td>
                </tr>
              ))}
            </Table>
          )}
          {scanTrip && (
            <div className="mt-3 rounded-lg border border-slate-200 p-3">
              <div className="mb-2 text-sm font-medium">Trip #{scanTrip}: scan the delivery QR on the driver&apos;s phone</div>
              <Scanner onCode={(code) => act(() => api(`/trips/${scanTrip}/scan/delivery`, { body: { token: code } }), "Arrival confirmed. Now weigh the lots below.").then(() => setScanTrip(null))} />
            </div>
          )}
        </Card>
        <Card title="Trucks on the way"><MapView markers={markers} fitKey={markers.length ? `n${markers.length}` : undefined} height={320} /></Card>
      </div>

      <Card title="At the gate: record weight and price">
        {!b.awaiting_weighing.length ? <Empty>Nothing waiting. Lots show up here after the delivery QR is scanned.</Empty> : (
          <Table head={["Lot", "Farmer", "Declared", "Grade", "Weight (kg)", "Price (Rs/quintal)", ""]}>
            {b.awaiting_weighing.map((l) => {
              const w = weigh[l.lot_id] || { kg: String(Math.round(l.declared_tons * 1000)), price: "" };
              return (
                <tr key={l.lot_id}>
                  <td className="px-2 py-1.5">#{l.lot_id}</td>
                  <td className="px-2 py-1.5">{l.farmer}</td>
                  <td className="px-2 py-1.5">{num(l.declared_tons, 2)} t</td>
                  <td className="px-2 py-1.5">{l.grade}</td>
                  <td className="px-2 py-1.5"><input className={`${inputCls} w-28`} type="number" value={w.kg} onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { ...w, kg: e.target.value } })} /></td>
                  <td className="px-2 py-1.5"><input className={`${inputCls} w-28`} type="number" value={w.price} onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { ...w, price: e.target.value } })} /></td>
                  <td className="px-2 py-1.5">
                    <Btn disabled={!Number(w.kg) || !Number(w.price)} onClick={() => act(() => api(`/trader/lots/${l.lot_id}/weigh`, { body: { weight_kg: Number(w.kg), price_per_quintal: Number(w.price) } }), `Lot #${l.lot_id} recorded; the farmer has been notified.`)}>Record</Btn>
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      <Card title="Delivered in the last 24 h">
        {!b.delivered_last_24h.length ? <Empty>None yet.</Empty> : (
          <Table head={["Lot", "Farmer", "Weight", "Price", "Value"]}>
            {b.delivered_last_24h.map((d) => (
              <tr key={d.lot_id}>
                <td className="px-2 py-1.5">#{d.lot_id}</td>
                <td className="px-2 py-1.5">{d.farmer}</td>
                <td className="px-2 py-1.5">{num(d.kg, 0)} kg</td>
                <td className="px-2 py-1.5">{rs(d.price_per_quintal)}/q</td>
                <td className="px-2 py-1.5">{rs((d.kg / 100) * d.price_per_quintal)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <PricePanel title="Prices across mandis" initialMandiId={user.mandi_id} />
    </div>
  );
}
