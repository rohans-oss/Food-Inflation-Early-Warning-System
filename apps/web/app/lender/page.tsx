"use client";

import { Fragment, useState } from "react";

import { Shell } from "@/components/Shell";
import { Badge, Card, ErrorNote, Note, SimBadge, StatusBadge, Table, Td, useApi } from "@/components/ui";
import { dateTime, inr, num, tons } from "@/lib/format";
import { useSession } from "@/lib/session";

const CHECK_LABEL: Record<string, string> = {
  pickup_qr: "Pickup QR scanned",
  delivery_qr: "Delivery QR scanned",
  gps_coverage_ge_80pct: "GPS coverage ≥ 80%",
  on_time: "Arrived within 45 min of plan",
  no_unexpected_stop: "No unexpected stop",
};

function Score({ v }: { v: number | null | undefined }) {
  if (v == null) return <Badge>No completed trip</Badge>;
  const kind = v >= 80 ? "good" : v >= 50 ? "warn" : "critical";
  return <Badge kind={kind}>{v}/100 {v >= 80 ? "reliable" : v >= 50 ? "partial" : "weak"}</Badge>;
}

export default function Lender() {
  const { t } = useSession();
  const d = useApi<any>("/lender/lots", { poll: 60000 });
  const [open, setOpen] = useState<number | null>(null);

  return (
    <Shell roles={["lender"]} title="Verified shipment history">
      <ErrorNote error={d.error} />
      <Note>{d.data?.note ?? "Only lots whose farmer chose to share them with your organization are listed."}</Note>
      <Card title="Farmers">
        <Table head={["Farmer", "Lots shared", "Delivered", `Avg ${t("reliability").toLowerCase()}`]}>
          {d.data?.farmers?.map((f: any) => (
            <tr key={f.farmer_id}><Td>{f.farmer}</Td><Td>{f.lots}</Td><Td>{f.delivered}</Td><Td><Score v={f.avg_trip_reliability} /></Td></tr>
          ))}
        </Table>
      </Card>
      <Card title="Lots">
        <Table head={["Lot", "Farmer", "Declared", "Pickup", "Route", t("delivery"), "Value", t("reliability")]}>
          {d.data?.lots?.map((l: any) => (
            <Fragment key={l.lot_id}>
              <tr className="cursor-pointer hover:bg-page" onClick={() => setOpen(open === l.lot_id ? null : l.lot_id)}>
                <Td>#{l.lot_id} <StatusBadge s={l.status} /></Td>
                <Td>{l.farmer}</Td>
                <Td>{tons(l.declared_tons)}</Td>
                <Td>{l.pickup.label || "–"}<div className="text-xs text-muted">{dateTime(l.pickup.scanned_at)}</div></Td>
                <Td>{l.route ? <>{l.route.vehicle} · {num(l.route.planned_km, 0)} km <SimBadge on={l.route.is_simulated} /></> : "–"}</Td>
                <Td>{l.delivery.mandi ?? "–"}<div className="text-xs text-muted">{l.delivery.weight_kg ? `${num(l.delivery.weight_kg, 0)} kg @ ${inr(l.delivery.price_per_quintal)}/q` : ""}</div></Td>
                <Td>{inr(l.delivery.value_rs)}</Td>
                <Td><Score v={l.reliability?.score} /></Td>
              </tr>
              {open === l.lot_id && l.reliability && (
                <tr><td colSpan={8} className="border-b border-line bg-page px-3 py-2 text-sm">
                  <ul className="grid gap-1 sm:grid-cols-2">
                    {Object.entries(l.reliability.checks).map(([k, ok]) => (
                      <li key={k}>{ok ? "✓" : "✗"} {CHECK_LABEL[k] ?? k}{k === "gps_coverage_ge_80pct" && l.reliability.gps_coverage != null && ` (${Math.round(l.reliability.gps_coverage * 100)}%)`}</li>
                    ))}
                  </ul>
                  <p className="mt-1 text-xs text-muted">Trip {dateTime(l.route?.started_at)} → {dateTime(l.route?.ended_at)}. Score = 25 pickup QR + 25 delivery QR + 20 GPS coverage + 15 on time + 15 no unexpected stop.</p>
                </td></tr>
              )}
            </Fragment>
          ))}
        </Table>
      </Card>
    </Shell>
  );
}
