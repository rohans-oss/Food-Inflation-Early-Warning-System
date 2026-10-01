"use client";

import { QR } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { Card, Note, Stat, StatusBadge, Table, Td, useApi } from "@/components/ui";
import { apiBase } from "@/lib/api";
import { day, inr, num, time } from "@/lib/format";

/** The driver's home: their record (trips, distance, tonnes, estimated earnings), current trips and history.
 * Driving itself happens in the phone app (GPS, offline buffer, QR, pickup code). */
export default function Driver() {
  const trips = useApi<any[]>("/trips", { query: { status: "assigned,accepted,in_progress" }, poll: 15000 });
  const s = useApi<any>("/driver/summary", { poll: 30000 });
  const pwa = `${apiBase()}/driver/`;
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

      <Card title="Current trips">
        <Table head={["Trip", "Vehicle", "Mandi", "Load", "Status", "ETA"]} empty="No trip right now. New trips from your fleet owner appear here and in the driver app.">
          {trips.data?.map((t) => (
            <tr key={t.id}>
              <Td>#{t.id}</Td><Td>{t.vehicle}</Td><Td>{t.mandi}</Td>
              <Td>{num(t.load_tons, 1)} t</Td><Td><StatusBadge s={t.status} /></Td><Td>{time(t.eta_at)}</Td>
            </tr>
          ))}
        </Table>
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
          <QR value={pwa} size={160} caption="Driver app" />
          <div className="max-w-md space-y-2 text-sm">
            <p>Scan this with your phone camera, sign in with the same account, then <b>Add to Home Screen</b>.</p>
            <p>The app shares your location <b>only while a trip is in progress</b> and after you tick consent. A red <b>TRACKING ON</b> bar is shown the whole time. Ending the trip stops it.</p>
            <p>At the farm, tell the farmer your <b>4-digit pickup code</b> from the app.</p>
            <a href={pwa} className="inline-block rounded-lg bg-brand px-3 py-2 font-medium text-brand-ink">Open driver app</a>
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
