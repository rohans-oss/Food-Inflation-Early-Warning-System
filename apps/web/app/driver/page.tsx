"use client";

import { useState } from "react";

import { DriverDirect } from "@/components/DriverDirect";
import { QR } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { Button, Card, ErrorNote, Note, Stat, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api, apiBase } from "@/lib/api";
import { day, inr, num, time } from "@/lib/format";

/** The driver's home: their record (trips, distance, tonnes, estimated earnings), current trips and history.
 * Driving itself happens in the phone app (GPS, offline buffer, QR, pickup code). */
export default function Driver() {
  const trips = useApi<any[]>("/trips", { query: { status: "assigned,accepted,in_progress" }, poll: 15000 });
  const s = useApi<any>("/driver/summary", { poll: 30000 });
  const reqs = useApi<any[]>("/driver/bookings", { poll: 8000 });
  const act = useAction();
  const accept = (id: number) => act.run(async () => {
    await api(`/driver/bookings/${id}/accept`, { method: "POST" });
    reqs.reload(); trips.reload(); s.reload();
  });
  const pwa = `${apiBase()}/driver/`;
  // "Drive in the app": a 2-minute single-use ticket in the link's #fragment signs the app in as you (no password)
  const openApp = (tripId?: number) => act.run(async () => {
    const { ticket } = await api<{ ticket: string }>("/auth/handoff-ticket", { method: "POST" });
    window.location.href = `${pwa}#handoff=${encodeURIComponent(ticket)}${tripId ? `&trip=${tripId}` : ""}`;
  });
  const [qrLink, setQrLink] = useState<string | null>(null);
  const showQr = () => act.run(async () => {
    const { ticket } = await api<{ ticket: string }>("/auth/handoff-ticket", { method: "POST" });
    setQrLink(`${pwa}#handoff=${encodeURIComponent(ticket)}`);
    setTimeout(() => setQrLink(null), 110000);
  });
  const d = s.data;
  return (
    <Shell roles={["driver"]} title="Driver">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Trips completed" value={d ? String(d.completed) : "–"} sub={d ? `${d.completed_month} this month` : undefined} />
        <Stat label="Distance driven" value={d ? `${num(d.km, 0)} km` : "–"} sub={d ? `${num(d.km_month, 0)} km this month` : undefined} />
        <Stat label="Produce delivered" value={d ? `${num(d.tons, 1)} t` : "–"} sub={d?.active ? `${d.active} trip${d.active > 1 ? "s" : ""} active now` : "no active trip"} />
        <Stat label="Earnings this month (est.)" value={d ? inr(d.earnings_month) : "–"} sub={d ? `${inr(d.earnings)} in total` : undefined} />
      </div>
      {d && <p className="text-xs text-muted">Earnings are estimated from your fleet&apos;s rates: {inr(d.rates.trip_allowance)} per trip + {inr(d.rates.per_km)} per km.
        Your fleet owner pays you; AgriPulse does not move money.</p>}

      <DriverDirect onAccepted={() => { trips.reload(); s.reload(); }} />

      <Card title={`Bookings for your company${reqs.data?.length ? ` (${reqs.data.length})` : ""}`}>
        <p className="mb-3 text-xs text-muted">Farmers who booked your transport company for a pickup time. Take a job and you become its driver; the farmer is told at once.</p>
        <ErrorNote error={act.error} />
        {reqs.data?.length === 0 && <p className="text-sm text-muted">No company bookings waiting.</p>}
        <div className="grid gap-3 lg:grid-cols-2">
          {reqs.data?.map((r) => (
            <div key={r.id} className="rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{r.farmer}</p>
                  <p className="text-sm text-ink2">{r.village ?? "Farm"}{r.farmer_district ? `, ${r.farmer_district}` : ""}
                    {r.farmer_phone && <> · <a className="underline" href={`tel:${r.farmer_phone}`}>{r.farmer_phone}</a></>}</p>
                </div>
                <Button disabled={act.busy} onClick={() => accept(r.id)}>Accept job</Button>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted">Produce</dt><dd>{r.crop} · {num(r.tons, 1)} t · {r.grade}</dd>
                <dt className="text-muted">Pickup</dt><dd>{r.pickup_local}</dd>
                <dt className="text-muted">Deliver to</dt><dd>{r.mandi}{r.mandi_district ? ` (${r.mandi_district})` : ""}</dd>
                <dt className="text-muted">Distance</dt><dd>~{num(r.road_km_approx, 0)} km farm → mandi</dd>
                <dt className="text-muted">Fare (est.)</dt><dd>{inr(r.fare_estimate)}</dd>
              </dl>
              <a className="mt-2 inline-block text-xs underline" target="_blank" rel="noreferrer"
                href={`https://www.openstreetmap.org/?mlat=${r.pickup_lat}&mlon=${r.pickup_lon}#map=14/${r.pickup_lat}/${r.pickup_lon}`}>Farm location on the map</a>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Current trips">
        {trips.data?.length === 0 && <p className="text-sm text-muted">No trip right now. Accept a request above, or wait for your fleet owner to assign one.</p>}
        <div className="space-y-3">
          {trips.data?.map((t) => (
            <div key={t.id} className="rounded-xl border border-line p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <b>Trip #{t.id}</b> · {t.vehicle} → {t.mandi} · {num(t.load_tons, 1)} t <StatusBadge s={t.status} />
                {t.eta_at && <span className="text-muted">ETA {time(t.eta_at)}</span>}
                <button onClick={() => openApp(t.id)} disabled={act.busy} className="ml-auto rounded-lg bg-brand px-3 py-1.5 font-medium text-brand-ink">Drive in the app</button>
              </div>
              {t.pickups?.map((p: any) => (
                <p key={p.lot_id} className="mt-2 text-ink2">
                  Pick up from <b className="text-ink">{p.farmer}</b>{p.village ? `, ${p.village}` : ""}{p.district ? ` (${p.district})` : ""}
                  {p.phone && <> · <a className="underline" href={`tel:${p.phone}`}>{p.phone}</a></>} · {p.crop} {num(p.tons, 1)} t
                  {p.pickup_at && <> · at {new Date(p.pickup_at).toLocaleString("en-IN", { weekday: "short", hour: "numeric", minute: "2-digit" })}</>}
                  {p.lat != null && !t.pickup_scanned_at && <> · <a className="font-medium text-brand underline" target="_blank" rel="noreferrer"
                    href={`https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}&travelmode=driving`}>Directions to the farm</a></>}
                </p>
              ))}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Trip history">
        <Table head={["Date", "From", "To", "Produce", "Distance", "Vehicle", "Status", "Pay (est.)"]} empty="No trips yet.">
          {d?.history?.map((h: any) => (
            <tr key={h.id}>
              <Td>{day(h.date)}</Td><Td>{h.from}</Td><Td>{h.mandi}</Td>
              <Td>{h.crops.join(", ")} · {num(h.tons, 1)} t</Td><Td>{num(h.km, 0)} km</Td><Td>{h.vehicle}</Td>
              <Td><StatusBadge s={h.status} /></Td><Td>{h.pay != null ? inr(h.pay) : "–"}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Open the driver app on your phone">
        <div className="flex flex-wrap items-center gap-6">
          <QR value={qrLink ?? pwa} size={160} caption={qrLink ? "Scan within 2 minutes: opens signed in" : "Driver app"} />
          <div className="max-w-md space-y-2 text-sm">
            <p>Scan this with your phone camera, sign in with the same account, then <b>Add to Home Screen</b>.</p>
            <p>The app shares your location <b>only while a trip is in progress</b> and after you tick consent. A red <b>TRACKING ON</b> bar is shown the whole time. Ending the trip stops it.</p>
            <p>At the farm, tell the farmer your <b>4-digit pickup code</b> from the app.</p>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => openApp()} disabled={act.busy} className="rounded-lg bg-brand px-3 py-2 font-medium text-brand-ink">Open driver app</button>
              <button onClick={showQr} disabled={act.busy} className="rounded-lg border border-line px-3 py-2 font-medium">QR that signs your phone in</button>
            </div>
          </div>
        </div>
        <div className="mt-4">
          <Note>
            Keep the app open on screen during a trip. Phone browsers pause GPS for background tabs, so the app holds a screen
            wake lock and buffers points offline; points recorded while the screen was locked are lost, not delayed.
          </Note>
        </div>
      </Card>
    </Shell>
  );
}
