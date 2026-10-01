"use client";

import { ago } from "@/lib/format";

import { Card, StatusBadge, Table, Td, useApi } from "./ui";

const LABEL: Record<string, string> = {
  direct: "Direct: farmer → online driver",
  farmer_company: "Farmer → transport company",
  fpo_fleet: "FPO → fleet → driver",
  demo: "Demo autopilot (sample)",
  unknown: "Unknown",
};

/** Admin: trips by how they were booked, real vs simulated, and the latest direct requests with how fast a driver
 * saw / answered them (the field-test numbers). */
export function BookingChannels() {
  const c = useApi<any>("/admin/booking-channels", { poll: 15000 });
  const d = c.data;
  return (
    <Card title="Trips by booking channel" action={d && <span className="text-xs text-muted">
      {d.drivers_online} driver{d.drivers_online === 1 ? "" : "s"} online now ({d.drivers_switched_on} switched on)</span>}>
      <Table head={["Channel", "Real trips", "Simulated trips", "Active", "Completed"]} empty="No trips yet.">
        {d?.channels.map((x: any) => (
          <tr key={x.channel}>
            <Td><b>{LABEL[x.channel] ?? x.channel}</b></Td><Td>{x.real}</Td><Td>{x.simulated}</Td><Td>{x.active}</Td><Td>{x.completed}</Td>
          </tr>
        ))}
      </Table>
      <h3 className="mb-2 mt-5 text-sm font-semibold">Latest direct requests</h3>
      <Table head={["#", "When", "Farmer", "To", "Status", "Drivers asked", "Push sent", "First seen", "Answered", "Trip"]}
        empty="No direct requests yet.">
        {d?.requests.map((r: any) => (
          <tr key={r.id}>
            <Td>{r.id}</Td><Td>{ago(r.created_at)}</Td><Td>{r.farmer}</Td><Td>{r.mandi} ({r.district})</Td>
            <Td><StatusBadge s={r.status} /></Td><Td>{r.drivers_notified}</Td><Td>{r.push_sent}</Td>
            <Td>{r.first_seen_s != null ? `${r.first_seen_s} s` : "–"}</Td><Td>{r.answered_s != null ? `${r.answered_s} s` : "–"}</Td>
            <Td>{r.trip_id ?? "–"}</Td>
          </tr>
        ))}
      </Table>
      <p className="mt-2 text-xs text-muted">Direct trips are always real (real accounts, real trucks, is_simulated = false). &quot;First seen&quot; = from
        sending to the first driver&apos;s phone fetching the request.</p>
    </Card>
  );
}
