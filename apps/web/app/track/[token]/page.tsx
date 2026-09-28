"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { SimBadge, Status } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { dateTime, num, time } from "@/lib/format";
import { useLive } from "@/lib/live";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

interface PublicTrip {
  status: string;
  lat: number | null;
  lon: number | null;
  last_seen_at: string | null;
  remaining_km: number | null;
  eta_at: string | null;
  eta_local: string | null;
  lots: { lot_id: number; status: string }[];
  is_simulated: boolean;
}

/**
 * Public tracking link: no login, nothing to install. The token is unguessable and expires.
 * Shows only position, ETA and lot status (rule 4).
 */
export default function PublicTrack() {
  const { token } = useParams<{ token: string }>();
  const [p, setP] = useState<PublicTrip | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<PublicTrip>(`/public/track/${token}`, { auth: false })
      .then(setP)
      .catch((e) => setError(e instanceof ApiError && e.status === 410 ? "This tracking link has expired." : e instanceof ApiError && e.status === 404 ? "Unknown tracking link." : e.message));
  }, [token]);

  useLive(error ? null : `/ws/track/${token}`, (msg) => {
    if (msg.type === "position") setP(msg as unknown as PublicTrip);
  }, { anonymous: true });

  const markers = useMemo(() => (p?.lat != null && p?.lon != null ? [{ id: "v", lat: p.lat, lon: p.lon, kind: "vehicle" as const, label: "Your produce", simulated: p.is_simulated }] : []), [p?.lat, p?.lon, p?.is_simulated]);

  return (
    <div className="mx-auto max-w-2xl space-y-3 px-4 py-6">
      <h1 className="text-xl font-bold text-green-800">AgriPulse · live delivery</h1>
      {error ? (
        <div className="rounded-md border border-slate-200 bg-white p-4 text-slate-700">{error}</div>
      ) : !p ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <Status s={p.status} />
              <SimBadge on={p.is_simulated} />
            </div>
            {p.status === "in_progress" ? (
              <div className="mt-2 text-2xl font-semibold text-slate-900">
                {p.remaining_km != null ? `${num(p.remaining_km, 0)} km away` : "Waiting for GPS"}
                {p.eta_at && <span className="block text-base font-normal text-slate-700">Arriving around {time(p.eta_at)}</span>}
              </div>
            ) : (
              <div className="mt-2 text-lg">{p.status === "completed" ? "Delivered. Trip completed." : `Trip ${p.status.replace("_", " ")}`}</div>
            )}
            <div className="mt-1 text-xs text-slate-500">Last update {dateTime(p.last_seen_at)}</div>
          </div>
          {p.status === "in_progress" && <MapView markers={markers} fitKey={markers.length ? "v" : undefined} height={360} />}
          <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
            <div className="mb-1 font-medium">Lots on this vehicle</div>
            {p.lots.map((l) => <div key={l.lot_id} className="flex justify-between"><span>Lot #{l.lot_id}</span><Status s={l.status} /></div>)}
          </div>
          <p className="text-xs text-slate-500">This link shows position, ETA and lot status only, and stops working after the trip.</p>
        </>
      )}
    </div>
  );
}
