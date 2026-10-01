"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useLiveFeed } from "@/lib/live";
import { inr, num } from "@/lib/format";

import { Button, inputCls, useApi } from "./ui";

/** Direct farmer -> driver booking: ask the drivers who are online right now for this district + mandi. The first to
 * accept becomes the driver; the answer arrives live over the websocket (and the parent polls as a fallback). */
export function DirectRequest({ lotId, mandi, request, busy, post, onLive }: {
  lotId: number;
  mandi: { id: number; name: string; district?: string };
  request: any | null;
  busy: boolean;
  post: (path: string, body?: unknown) => void;
  onLive: () => void;
}) {
  const districts = useApi<string[]>("/auth/districts");
  const mandiDistrict = mandi.district ?? "";
  const [district, setDistrict] = useState("");
  useEffect(() => { if (!district && mandiDistrict) setDistrict(mandiDistrict); }, [mandiDistrict, district]);
  const avail = useApi<any>(district ? `/lots/${lotId}/drivers-available` : null,
    { query: { mandi_id: mandi.id, district }, poll: 10000 });
  useLiveFeed((m) => { if (m.type === "trip_request_update" && m.lot_id === lotId) onLive(); });
  const [, tick] = useState(0);
  const waiting = request?.status === "notified";
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, [waiting]);

  if (waiting) {
    const left = Math.max(0, Math.round((new Date(request.expires_at).getTime() - Date.now()) / 1000));
    return (
      <div className="rounded-xl border border-brand p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="flex items-center gap-2 font-semibold">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />
              Asking {request.drivers_notified} driver{request.drivers_notified === 1 ? "" : "s"} near you…
            </p>
            <p className="mt-1 text-sm text-ink2">
              To {request.mandi} ({request.district}) · ~{num(request.estimated_km, 0)} km
              {request.estimated_fare ? ` · fare about ${inr(request.estimated_fare)}` : ""}
            </p>
            <p className="mt-1 text-xs text-muted">
              {request.drivers_seen ? `Seen by ${request.drivers_seen}` : "Sent to their phones"}
              {request.drivers_declined ? ` · ${request.drivers_declined} said no` : ""} ·{" "}
              <span className="font-mono">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</span> left
            </p>
          </div>
          <Button variant="secondary" disabled={busy} onClick={() => post(`/driver-requests/${request.id}/cancel`)}>Cancel</Button>
        </div>
      </div>
    );
  }

  const failed = request && ["no_drivers", "declined", "expired"].includes(request.status);
  const n = avail.data?.drivers;
  return (
    <div className="rounded-xl border border-brand/50 bg-brand/5 p-4">
      <h3 className="font-semibold">Request a driver near you</h3>
      <p className="mt-0.5 text-sm text-ink2">Drivers who are online now get your request on their phone at once. The first to accept comes to your farm.</p>
      {failed && (
        <p className="mt-3 rounded-lg border border-critical/40 px-3 py-2 text-sm text-critical">
          {request.reason ?? "No driver took the trip."} Try again in a few minutes, or book a transport company below.
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-muted">District</span>
          <select className={`${inputCls} w-48`} value={district} onChange={(e) => setDistrict(e.target.value)}>
            {(districts.data ?? []).map((x) => <option key={x}>{x}</option>)}
          </select>
        </label>
        <div className="text-sm">
          <span className="mb-1 block text-xs text-muted">Mandi</span>
          <p className="py-2 font-medium">{mandi.name}</p>
        </div>
        <Button disabled={busy || !district} onClick={() => post(`/lots/${lotId}/driver-request`, { mandi_id: mandi.id, district })}>
          {busy ? "Sending…" : failed ? "Try again" : "Request a driver"}
        </Button>
      </div>
      <p className={`mt-2 text-xs ${n ? "text-brand" : "text-muted"}`}>
        {n == null ? "Checking who is online…"
          : n ? `${n} driver${n === 1 ? " is" : "s are"} online for ${mandi.name} right now`
            : `No drivers are currently available for ${mandi.name}. You can still send the request, or book a transport company below.`}
      </p>
    </div>
  );
}
