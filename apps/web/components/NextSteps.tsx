"use client";

import { api } from "@/lib/api";
import { ago, inr, num, tons } from "@/lib/format";

import { Button, Card, ErrorNote, ProvenanceBadge, SimBadge, Table, Td, useAction, useApi } from "./ui";

const STEPS: [key: string, label: string, hint: string][] = [
  ["mandi_chosen", "Mandi chosen", "Pick one with Sell here"],
  ["transport_requested", "Transport requested", "Ask your FPO to ship it"],
  ["grouped", "Shipment created", "Your FPO groups the lot"],
  ["fleet_booked", "Fleet booked", "The FPO books a transporter"],
  ["truck_assigned", "Truck assigned", "A truck and driver are set"],
  ["picked_up", "Picked up", "The driver scans your QR"],
  ["delivered", "Delivered", "Weighed and priced at the mandi"],
];

/** After a mandi is chosen: the route and cost there, which transporters have a free truck that fits, the request
 * to the FPO, and the steps that follow. */
export function NextSteps({ lotId, tonsLot, onChanged }: { lotId: number; tonsLot: number; onChanged: () => void }) {
  const ns = useApi<any>(`/lots/${lotId}/next-steps`, { poll: 20000 });
  const act = useAction();
  const d = ns.data;
  if (!d) return <ErrorNote error={ns.error} />;
  const r = d.route;
  const current = STEPS.findIndex(([k]) => !d.done[k]);

  return (
    <Card title="Next steps" action={r && <ProvenanceBadge p={r.data_provenance} />}>
      <ol className="mb-5 grid gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {STEPS.map(([k, label, hint], i) => {
          const done = d.done[k];
          const now = i === current;
          return (
            <li key={k} className={`rounded-lg border px-3 py-2 text-xs ${done ? "border-brand/40 bg-brand/10" : now ? "border-brand" : "border-line"}`}>
              <p className={`font-semibold ${done ? "text-brand" : now ? "text-ink" : "text-muted"}`}>{done ? "✓ " : `${i + 1}. `}{label}</p>
              {!done && <p className="mt-0.5 text-muted">{hint}</p>}
            </li>
          );
        })}
      </ol>

      {!d.mandi && <p className="text-sm text-ink2">Choose a mandi above with <b>Sell here</b> to see the route, transport and the next steps.</p>}

      {d.mandi && (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-4">
            <Info label="Mandi" value={d.mandi.name} sub={d.mandi_is_final ? "booked in a shipment" : "your choice"} />
            <Info label="Road" value={r ? `${num(r.road_km, 0)} km` : "–"} sub={r ? `${num(r.drive_hours, 1)} h${r.route_source !== "osrm" ? " · approx." : ""}` : "no forecast for this mandi yet"} />
            <Info label="Transport (est.)" value={r ? inr(r.transport_cost) : "–"}
              sub={r?.vehicle_assumption ? `hired ${r.vehicle_assumption.capacity_tons} t truck, ₹${num(r.vehicle_assumption.rate_per_km, 0)}/km${r.vehicle_assumption.return_leg ? ", both ways" : ""}` : undefined} />
            <Info label="Net value p50" value={r ? inr(r.net_value?.p50) : "–"} sub={r ? `${inr(r.net_value?.p10)}–${inr(r.net_value?.p90)}` : undefined} />
          </div>
          {r && r.feasible === false && <p className="text-sm text-critical">Not advised: {r.why_not?.join("; ")}</p>}

          <div>
            <h3 className="mb-2 text-sm font-semibold">Transport available now</h3>
            <Table head={["Transporter", "Free trucks that fit " + tons(tonsLot), "Free / total", "Sizes"]} empty="No transporters registered yet.">
              {d.fleets.map((f: any) => (
                <tr key={f.org_id}>
                  <Td>{f.name} <SimBadge on={f.is_simulated} /></Td>
                  <Td><b className={f.free_that_fit ? "text-brand" : "text-muted"}>{f.free_that_fit}</b></Td>
                  <Td>{f.free} / {f.vehicles}</Td>
                  <Td className="text-ink2">{f.capacities_tons.length ? f.capacities_tons.map((c: number) => `${num(c, 1)} t`).join(", ") : "–"}</Td>
                </tr>
              ))}
            </Table>
            <p className="mt-1 text-xs text-muted">Live from the transporters&apos; vehicles; a truck on an active trip isn&apos;t counted. Your FPO books the transporter.</p>
          </div>

          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-page p-3">
            {d.done.grouped ? (
              <p className="text-sm">Your FPO has put this lot in a shipment{d.done.fleet_booked ? " and booked a transporter" : ""}. You&apos;ll get an alert when the truck is on its way.</p>
            ) : d.transport_requested_at ? (
              <p className="text-sm"><b className="text-brand">✓ Transport requested</b> {ago(d.transport_requested_at)} · {d.fpo?.name} has been notified and will group the lot and book a truck.</p>
            ) : d.fpo ? (
              <>
                <p className="flex-1 text-sm">Ready? Ask <b>{d.fpo.name}</b> to ship this lot to <b>{d.mandi.name}</b>.</p>
                <Button disabled={!d.can_request || act.busy}
                  onClick={() => act.run(async () => { await api(`/lots/${lotId}/request-transport`, { method: "POST" }); ns.reload(); onChanged(); })}>
                  {act.busy ? "Sending…" : "Request transport"}
                </Button>
              </>
            ) : (
              <p className="text-sm text-ink2">This lot isn&apos;t linked to an FPO, so there is nobody to arrange the transport yet.</p>
            )}
          </div>
          <ErrorNote error={act.error} />
        </div>
      )}
    </Card>
  );
}

function Info({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 font-semibold">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-ink2">{sub}</p>}
    </div>
  );
}
