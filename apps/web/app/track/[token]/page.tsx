"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { MapView } from "@/components/MapView";
import { StatusBadge } from "@/components/ui";
import { api, wsUrl } from "@/lib/api";
import { ago, num } from "@/lib/format";

interface Pub {
  status: string;
  lat: number | null;
  lon: number | null;
  last_seen_at: string | null;
  remaining_km: number | null;
  eta_local: string | null;
  lots: { lot_id: number; status: string }[];
  is_simulated: boolean;
}

/** Public tracking page: no login. Shows only what the API exposes for a share token. */
export default function Track() {
  const { token } = useParams<{ token: string }>();
  const [d, setD] = useState<Pub | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    api<Pub>(`/public/track/${token}`, { auth: false }).then(setD).catch((e) =>
      setErr(e.status === 410 ? "This tracking link has expired." : e.status === 404 ? "This tracking link is not valid." : e.message));
    const open = () => {
      ws = new WebSocket(wsUrl(`/ws/public/${token}`, {}));
      ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.type === "position") setD(msg); };
      ws.onclose = (ev) => {
        if (ev.code === 4410) { setErr((e) => e ?? "This tracking link has expired."); return; }
        if (!closed) setTimeout(open, 5000);
      };
    };
    open();
    return () => { closed = true; ws?.close(); };
  }, [token]);

  return (
    <main className="mx-auto max-w-2xl space-y-4 px-4 py-6">
      <div className="flex items-center gap-2 font-semibold">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-brand-ink">A</span> AgriPulse · Shipment tracking
      </div>
      {err && <p className="rounded-lg border border-line p-4">{err}</p>}
      {d && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge s={d.status} />
            <span className="text-sm text-ink2">Updated {ago(d.last_seen_at)}</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-line bg-surface p-4"><div className="text-sm text-ink2">Arriving</div><div className="text-2xl font-semibold">{d.eta_local ?? "–"}</div></div>
            <div className="rounded-xl border border-line bg-surface p-4"><div className="text-sm text-ink2">Distance left</div><div className="text-2xl font-semibold">{d.remaining_km != null ? `${num(d.remaining_km, 0)} km` : "–"}</div></div>
          </div>
          {d.lat != null && d.lon != null ? (
            <MapView height="h-96" fitKey={`${d.lat},${d.lon}`} zoom={10} center={[d.lon, d.lat]}
              markers={[{ id: "v", lat: d.lat, lon: d.lon, kind: "vehicle", label: "Vehicle" }]} />
          ) : <p className="text-sm text-muted">Position is shown only while the trip is in progress.</p>}
          <ul className="text-sm">
            {d.lots.map((l) => <li key={l.lot_id} className="flex justify-between border-b border-line py-2"><span>Lot #{l.lot_id}</span><StatusBadge s={l.status} /></li>)}
          </ul>
          <p className="text-xs text-muted">This link shows only the vehicle position, arrival time and lot status, and expires automatically.</p>
        </>
      )}
    </main>
  );
}
