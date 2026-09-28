"use client";

import { Fragment, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Card, Empty, ErrorNote, SimBadge, Status, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, num, rs } from "@/lib/format";

interface Rel { score: number; checks: Record<string, boolean>; gps_coverage: number | null; events: string[] }
interface LenderLot {
  lot_id: number; farmer_id: number; farmer: string; crop: string; declared_tons: number; status: string;
  pickup: { label: string; scanned_at: string | null };
  route: { planned_km: number | null; started_at: string | null; ended_at: string | null; vehicle: string; is_simulated: boolean } | null;
  delivery: { mandi: string | null; scanned_at: string | null; weight_kg: number | null; price_per_quintal: number | null; value_rs: number | null };
  reliability: Rel | null;
}
interface Resp { lots: LenderLot[]; farmers: { farmer_id: number; farmer: string; lots: number; delivered: number; avg_trip_reliability: number | null }[]; note: string }

const CHECK_LABEL: Record<string, string> = {
  pickup_qr: "Pickup QR scanned",
  delivery_qr: "Delivery QR scanned",
  gps_coverage_ge_80pct: "GPS covered ≥ 80% of trip",
  on_time: "Arrived within 45 min of plan",
  no_unexpected_stop: "No unexplained stop",
};

function scoreColor(s: number | null | undefined) {
  if (s == null) return "text-slate-500";
  return s >= 80 ? "text-green-700" : s >= 50 ? "text-amber-700" : "text-red-700";
}

export default function LenderPage() {
  return <Shell roles={["lender"]} title="Lender / insurer">{() => <Lender />}</Shell>;
}

function Lender() {
  const [d, setD] = useState<Resp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [farmer, setFarmer] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { api<Resp>("/lender/lots").then(setD).catch((e) => setError(e.message)); }, []);

  if (!d) return <><ErrorNote error={error} /><Empty>Loading…</Empty></>;
  const lots = farmer ? d.lots.filter((l) => l.farmer_id === farmer) : d.lots;

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">{d.note}</p>
      <Card title="Farmers">
        {!d.farmers.length ? <Empty>No farmer has shared a lot with your organization yet.</Empty> : (
          <Table head={["Farmer", "Lots shared", "Delivered", "Avg trip reliability", ""]}>
            {d.farmers.map((f) => (
              <tr key={f.farmer_id} className={farmer === f.farmer_id ? "bg-green-50" : ""}>
                <td className="px-2 py-1.5">{f.farmer}</td>
                <td className="px-2 py-1.5">{f.lots}</td>
                <td className="px-2 py-1.5">{f.delivered}</td>
                <td className={`px-2 py-1.5 font-semibold ${scoreColor(f.avg_trip_reliability)}`}>{f.avg_trip_reliability ?? "–"}{f.avg_trip_reliability != null && "/100"}</td>
                <td className="px-2 py-1.5"><button className="text-green-800 underline" onClick={() => setFarmer(farmer === f.farmer_id ? null : f.farmer_id)}>{farmer === f.farmer_id ? "Show all" : "Filter"}</button></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card title="Verified shipment history (pickup → route → delivery → weight)">
        {!lots.length ? <Empty>Nothing to show.</Empty> : (
          <Table head={["Lot", "Farmer", "Declared", "Pickup QR", "Route", "Delivery QR", "Weighed", "Value", "Reliability"]}>
            {lots.map((l) => (
              <Fragment key={l.lot_id}>
                <tr className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(open === l.lot_id ? null : l.lot_id)}>
                  <td className="px-2 py-1.5">#{l.lot_id} <Status s={l.status} /></td>
                  <td className="px-2 py-1.5">{l.farmer}</td>
                  <td className="px-2 py-1.5">{num(l.declared_tons, 2)} t</td>
                  <td className="px-2 py-1.5 text-xs">{dateTime(l.pickup.scanned_at)}</td>
                  <td className="px-2 py-1.5 text-xs">{l.route ? <>{l.route.vehicle}<SimBadge on={l.route.is_simulated} />, {num(l.route.planned_km, 0)} km</> : "–"}</td>
                  <td className="px-2 py-1.5 text-xs">{dateTime(l.delivery.scanned_at)}<div>{l.delivery.mandi}</div></td>
                  <td className="px-2 py-1.5">{l.delivery.weight_kg ? `${num(l.delivery.weight_kg, 0)} kg` : "–"}</td>
                  <td className="px-2 py-1.5">{rs(l.delivery.value_rs)}</td>
                  <td className={`px-2 py-1.5 font-semibold ${scoreColor(l.reliability?.score)}`}>{l.reliability ? `${l.reliability.score}/100` : "–"}</td>
                </tr>
                {open === l.lot_id && l.reliability && (
                  <tr>
                    <td colSpan={9} className="bg-slate-50 px-4 py-2 text-xs">
                      <ul className="grid gap-1 sm:grid-cols-3">
                        {Object.entries(l.reliability.checks).map(([k, v]) => <li key={k}>{v ? "✅" : "❌"} {CHECK_LABEL[k] || k}</li>)}
                      </ul>
                      <div className="mt-1 text-slate-500">GPS coverage {l.reliability.gps_coverage == null ? "–" : `${Math.round(l.reliability.gps_coverage * 100)}%`} · events: {l.reliability.events.join(", ") || "none"} · trip {dateTime(l.route?.started_at)} → {dateTime(l.route?.ended_at)}</div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </Table>
        )}
        <p className="mt-2 text-xs text-slate-500">Score = pickup QR 25 + delivery QR 25 + GPS coverage 20 + on time 15 + no unexpected stop 15. Simulated trips are labelled and should not be used for credit decisions.</p>
      </Card>
    </div>
  );
}
