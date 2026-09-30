"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { dateTime, inr, num } from "@/lib/format";

import { Badge, Button, Card, ErrorNote, inputCls, Note, ProvenanceBadge, SimBadge, Table, Td, useAction, useApi } from "./ui";

/** V3-1 proposals: the optimizer suggests, a person accepts or rejects. Shown only when switched on in
 * config/recommender.toml (pre-registered switch, docs/optimizer-results.md). */

function Decide({ p, onDone }: { p: any; onDone: () => void }) {
  const act = useAction();
  const [why, setWhy] = useState("");
  if (p.status !== "proposed") {
    return <p className="text-sm text-ink2">Proposal #{p.id} {p.status}{p.decided_at ? ` · ${dateTime(p.decided_at)}` : ""}{p.reject_reason ? ` · “${p.reject_reason}”` : ""}</p>;
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={() => act.run(async () => { await api(`/loads/proposals/${p.id}/accept`, { method: "POST" }); onDone(); })} disabled={act.busy}>Accept</Button>
      <div className="w-56"><input className={inputCls} placeholder="Reason to reject (optional)" value={why} maxLength={200} onChange={(e) => setWhy(e.target.value)} /></div>
      <Button variant="secondary" onClick={() => act.run(async () => { await api(`/loads/proposals/${p.id}/reject`, { method: "POST", body: { reason: why || null } }); onDone(); })} disabled={act.busy}>Reject</Button>
      <ErrorNote error={act.error} />
    </div>
  );
}

export function SharedLoadsCard({ onChanged }: { onChanged?: () => void }) {
  const cfg = useApi<any>("/loads/config");
  const list = useApi<any[]>("/loads/proposals", { query: { kind: "consolidation" } });
  const act = useAction();
  if (!cfg.data?.consolidation) return null;
  const p = list.data?.[0];
  const plan = () => act.run(async () => { await api("/loads/plan", { method: "POST", body: {} }); list.reload(); });
  const done = () => { list.reload(); onChanged?.(); };
  return (
    <Card title="Plan shared truckloads" action={<Button onClick={plan} disabled={act.busy}>{act.busy ? "Planning…" : "Plan loads"}</Button>}>
      <p className="mb-3 text-sm text-ink2">Groups your members&apos; waiting lots onto shared hired trucks when that pays, within the spoilage
        and mandi-room limits. Nothing changes until you accept: then each load becomes a shipment.</p>
      <ErrorNote error={act.error} />
      {p && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <b>Saving vs one truck per lot: {inr(p.est_saving)}</b>
            <span className="text-ink2">· {p.totals.trucks_used} trucks instead of {p.baseline.trucks_used} · {num(p.totals.vehicle_km, 0)} km instead of {num(p.baseline.vehicle_km, 0)}</span>
            <ProvenanceBadge p={p.data_provenance} compact /><SimBadge on={p.is_simulated} />
            <Badge>{p.distances}</Badge>
          </div>
          <Table head={["Load", "Lots (pickup order)", "Mandi", "Tonnes", "Truck", "Net value (p50)", "Transport"]}>
            {p.loads.map((L: any, i: number) => (
              <tr key={i}>
                <Td>{i + 1} {L.shared && <Badge kind="good">shared</Badge>}</Td>
                <Td>{L.lot_ids.map((x: number) => `#${x}`).join(" → ")}</Td>
                <Td>{L.mandi}</Td><Td>{num(L.tons, 1)} t</Td><Td>{L.truck_tons} t hired</Td>
                <Td>{inr(L.est_net_p50)}</Td><Td>{inr(L.est_transport)}</Td>
              </tr>
            ))}
          </Table>
          {p.unserved_lot_ids?.length > 0 && <Note>Not worth shipping at today&apos;s forecast: {p.unserved_lot_ids.map((x: number) => `#${x}`).join(", ")}.</Note>}
          <p className="text-xs text-muted">Estimated at the forecast p50 (a plan, not a result). Pickups are listed in driving order; the trip still
            tracks one pickup point, so shared loads stay within about 20 km.</p>
          <Decide p={p} onDone={done} />
        </div>
      )}
    </Card>
  );
}

export function ReturnLoadsCard({ onChanged }: { onChanged?: () => void }) {
  const cfg = useApi<any>("/loads/config");
  const list = useApi<any[]>("/loads/proposals", { query: { kind: "return_load" } });
  const act = useAction();
  if (!cfg.data?.return_loads) return null;
  const p = list.data?.[0];
  const find = () => act.run(async () => { await api("/fleet/return-loads", { method: "POST" }); list.reload(); });
  const done = () => { list.reload(); onChanged?.(); };
  return (
    <Card title="Return loads" action={<Button onClick={find} disabled={act.busy}>{act.busy ? "Searching…" : "Find return loads"}</Button>}>
      <p className="mb-3 text-sm text-ink2">For trucks that delivered today: a booked shipment to carry instead of driving home empty.
        Accepting assigns the same truck and driver.</p>
      <ErrorNote error={act.error} />
      {p && (
        <div className="space-y-3">
          {p.matches.length === 0 ? <p className="text-sm text-muted">No return load pays today ({p.trucks_considered} trucks, {p.jobs_considered} open bookings checked).</p> : (
            <>
              <p className="text-sm"><b>Estimated saving: {inr(p.est_saving)}</b> <span className="text-ink2">in empty running</span></p>
              <Table head={["Truck", "Delivered at", "Next load", "To", "Tonnes", "Empty km saved", "Driver day"]}>
                {p.matches.map((m: any) => (
                  <tr key={m.trip_id}>
                    <Td>{m.vehicle} <SimBadge on={m.is_simulated} /></Td><Td>{m.from_mandi}</Td><Td>Shipment #{m.shipment_id}</Td><Td>{m.to_mandi}</Td>
                    <Td>{num(m.tons, 1)} / {m.truck_tons} t</Td><Td>{num(m.empty_km_saved, 0)} km ({inr(m.rs_saved)})</Td><Td>{m.day_hours} h</Td>
                  </tr>
                ))}
              </Table>
            </>
          )}
          <p className="text-xs text-muted">{p.home_assumption}; {p.distances}.</p>
          {p.matches.length > 0 && <Decide p={p} onDone={done} />}
        </div>
      )}
    </Card>
  );
}
