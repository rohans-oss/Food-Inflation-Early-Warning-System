"use client";

import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Card, Empty, SimBadge, Status, Table } from "@/components/ui";
import { api, API_URL, type TripDetail } from "@/lib/api";
import { dateTime, num } from "@/lib/format";

export default function DriverPage() {
  return <Shell roles={["driver"]} title="Transporter / driver">{() => <Driver />}</Shell>;
}

function Driver() {
  const [trips, setTrips] = useState<TripDetail[] | null>(null);
  useEffect(() => { api<TripDetail[]>("/trips").then(setTrips).catch(() => setTrips([])); }, []);
  const pwa = `${API_URL}/driver/`;
  return (
    <div className="space-y-4">
      <Card title="Open the driver app on your phone">
        <p className="text-sm text-slate-700">
          Trips, GPS sharing and QR scans run in the <b>AgriPulse Driver</b> app. It works offline and keeps your points until the network is back.
          Open it on your phone and use <i>Add to Home screen</i>:
        </p>
        <a href={pwa} className="mt-2 inline-block rounded-md bg-green-700 px-4 py-2 font-medium text-white">Open driver app</a>
        <p className="mt-2 text-xs text-slate-500">
          {pwa} · Location is shared only while a trip is in progress and you have ticked consent. The app shows a &quot;Tracking ON&quot; banner the whole time.
        </p>
      </Card>
      <Card title="My trips">
        {!trips?.length ? <Empty>No trips assigned yet.</Empty> : (
          <Table head={["Trip", "Vehicle", "To", "Load", "Status", "Started", "Ended"]}>
            {trips.map((t) => (
              <tr key={t.id}>
                <td className="px-2 py-1.5">#{t.id}<SimBadge on={t.is_simulated} /></td>
                <td className="px-2 py-1.5">{t.vehicle}</td>
                <td className="px-2 py-1.5">{t.mandi}</td>
                <td className="px-2 py-1.5">{num(t.load_tons, 2)} t</td>
                <td className="px-2 py-1.5"><Status s={t.status} /></td>
                <td className="px-2 py-1.5">{dateTime(t.started_at)}</td>
                <td className="px-2 py-1.5">{dateTime(t.ended_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
