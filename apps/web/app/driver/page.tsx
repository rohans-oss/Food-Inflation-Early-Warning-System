"use client";

import { QR } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { Card, Note, SimBadge, StatusBadge, Table, Td, useApi } from "@/components/ui";
import { apiBase } from "@/lib/api";
import { num, time } from "@/lib/format";

/** Drivers work in the PWA (GPS, offline buffer, QR). This page hands them the link and lists their trips. */
export default function Driver() {
  const trips = useApi<any[]>("/trips", { query: { status: "assigned,accepted,in_progress,completed" }, poll: 20000 });
  const pwa = `${apiBase()}/driver/`;
  return (
    <Shell roles={["driver"]} title="Driver">
      <Card title="Open the driver app on your phone">
        <div className="flex flex-wrap items-center gap-6">
          <QR value={pwa} size={180} caption="Driver app" />
          <div className="max-w-md space-y-2 text-sm">
            <p>Scan this with your phone camera, sign in with the same account, then <b>Add to Home Screen</b>.</p>
            <p>The app shares your location <b>only while a trip is in progress</b> and after you tick consent. A red <b>TRACKING ON</b> bar is shown the whole time. Ending the trip stops it.</p>
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
      <Card title="My trips">
        <Table head={["Trip", "Vehicle", "Mandi", "Load", "Status", "ETA"]} empty="No trips assigned yet.">
          {trips.data?.map((t) => (
            <tr key={t.id}>
              <Td>#{t.id} <SimBadge on={t.is_simulated} /></Td><Td>{t.vehicle}</Td><Td>{t.mandi}</Td>
              <Td>{num(t.load_tons, 1)} t</Td><Td><StatusBadge s={t.status} /></Td><Td>{time(t.eta_at)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </Shell>
  );
}
