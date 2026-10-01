"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { ago, dateTime, day, inr, num, tons } from "@/lib/format";

import { Button, Card, ErrorNote, inputCls, ProvenanceBadge, useAction, useApi } from "./ui";

const STEPS: [key: string, label: string, hint: string][] = [
  ["mandi_chosen", "Mandi chosen", "Pick one with Sell here"],
  ["transport_booked", "Transport booked", "Choose a transporter and time"],
  ["truck_assigned", "Truck confirmed", "The transporter assigns a truck"],
  ["picked_up", "Picked up", "Enter the driver's code"],
  ["at_mandi", "At the mandi", "Delivery QR scanned at the gate"],
  ["sold", "Weighed & sold", "The trader weighs and prices it"],
  ["paid", "Paid", "Payment recorded, you confirm"],
];

/** The farmer's path after choosing a mandi: book a transporter for a pickup slot, follow the booking, then the
 * payment. Live tracking is the "Live vehicle" card on the lot page once a truck is assigned. */
export function NextSteps({ lotId, tonsLot, onChanged }: { lotId: number; tonsLot: number; onChanged: () => void }) {
  const ns = useApi<any>(`/lots/${lotId}/next-steps`, { poll: 4000 });
  const lot = useApi<any>(`/lots/${lotId}`, { poll: 10000 });
  const act = useAction();
  const [open, setOpen] = useState<number | null>(null);
  const [slot, setSlot] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const d = ns.data;
  if (!d) return <ErrorNote error={ns.error} />;
  const r = d.route;
  const b = d.booking;
  const p = d.pickup;
  const l = lot.data;
  const current = STEPS.findIndex(([k]) => !d.done[k]);
  const refresh = () => { ns.reload(); lot.reload(); onChanged(); };
  const post = (path: string, body?: unknown) => act.run(async () => { await api(path, { method: "POST", body }); refresh(); });

  return (
    <Card title="Next steps" action={r?.data_provenance && <ProvenanceBadge p={r.data_provenance} />}>
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
      <ErrorNote error={act.error} />

      {!d.mandi && <p className="text-sm text-ink2">Choose a mandi above with <b>Sell here</b> to see the route, transporters and the next steps.</p>}

      {d.mandi && (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-4">
            <Info label="Mandi" value={d.mandi.name} sub={d.mandi_is_final ? "booked" : "your choice"} />
            <Info label="Road" value={r ? `${num(r.road_km, 0)} km` : "–"} sub={r ? `${num(r.drive_hours, 1)} h${r.route_source !== "osrm" ? " · approx." : ""}` : undefined} />
            {b && !["declined", "cancelled"].includes(b.status)
              ? <Info label="Transport fare (est.)" value={inr(b.fare_estimate)} sub={`booked with ${b.fleet}`} />
              : <Info label="Transport (est.)" value={r ? inr(r.transport_cost) : "–"} sub="hired truck, before you pick a transporter" />}
            {r?.net_value
              ? <Info label="Net value p50" value={inr(r.net_value.p50)} sub={`${inr(r.net_value.p10)}–${inr(r.net_value.p90)}`} />
              : r?.value_at_today_price != null
                ? <Info label="Value at latest price" value={inr(r.value_at_today_price)}
                    sub={`${inr(r.price_today.modal)}/q on ${day(r.price_today.date)} (Agmarknet) · not a forecast`} />
                : <Info label="Net value" value="–" sub="no price reported for this vegetable here" />}
          </div>

          {/* 1. choose a transporter and a pickup time */}
          {d.can_book && (!b || ["declined", "cancelled"].includes(b.status)) && (
            <div>
              {b && <p className="mb-3 rounded-lg border border-critical/40 px-3 py-2 text-sm text-critical">
                Your booking with {b.fleet} was {b.status}{b.reason ? `: ${b.reason}` : ""}. Choose another transporter or time.</p>}
              <h3 className="mb-1 font-semibold">Choose a transporter</h3>
              {d.fleets.length > 1 && <p className="mb-2 text-xs text-muted">{d.fleets.length} transporters near your farm, cheapest first. The fare includes the truck&apos;s drive from its base to your farm and back.</p>}
              <div className="space-y-3">
                {d.fleets.length === 0 && <p className="text-sm text-muted">No transporters have registered trucks yet.</p>}
                {d.fleets.map((f: any) => (
                  <div key={f.org_id} className={`rounded-xl border p-4 ${open === f.org_id ? "border-brand" : "border-line"}`}>
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                      <div className="min-w-48 flex-1">
                        <p className="font-semibold">{f.name}</p>
                        <p className="text-xs text-ink2">{f.fit} truck{f.fit === 1 ? "" : "s"} that fit {tons(tonsLot)} · {f.capacities_tons.map((c: number) => `${num(c, 1)} t`).join(", ") || "none"}</p>
                        {f.base && <p className="text-xs text-muted">Based in {f.base}{f.base_km_from_farm != null ? ` · about ${num(f.base_km_from_farm, 0)} km from your farm` : ""}</p>}
                        {f.drivers?.length > 0 && <p className="text-xs text-muted">Drivers: {f.drivers.join(", ")}</p>}
                      </div>
                      <div><p className="text-xs text-muted">Fare (est.)</p><p className="font-semibold">{inr(f.fare_estimate)}</p>
                        {f.base && <p className="text-[11px] text-muted">incl. trip from {f.base}</p>}</div>
                      <div><p className="text-xs text-muted">Open slots</p><p className="font-semibold">{f.free_slots}</p></div>
                      <Button variant={open === f.org_id ? "secondary" : "primary"} disabled={!f.free_slots}
                        onClick={() => { setOpen(open === f.org_id ? null : f.org_id); setSlot(null); }}>
                        {open === f.org_id ? "Close" : "Choose time"}
                      </Button>
                    </div>
                    {open === f.org_id && (
                      <div className="mt-4">
                        <p className="mb-2 text-sm text-ink2">Pickup time at your farm:</p>
                        <div className="flex flex-wrap gap-2">
                          {f.slots.map((s: any) => (
                            <button key={s.pickup_at} disabled={s.free_trucks < 1} onClick={() => setSlot(s.pickup_at)}
                              className={`rounded-lg border px-3 py-1.5 text-sm ${slot === s.pickup_at ? "border-brand bg-brand text-brand-ink"
                                : "border-line hover:border-brand"} disabled:cursor-not-allowed disabled:opacity-40`}>
                              {s.label}{s.free_trucks < 1 ? " · full" : ""}
                            </button>
                          ))}
                        </div>
                        <div className="mt-4 flex flex-wrap items-center gap-3">
                          <Button disabled={!slot || act.busy} onClick={() => post(`/lots/${lotId}/bookings`, { fleet_org_id: f.org_id, pickup_at: slot })}>
                            {act.busy ? "Booking…" : slot ? `Book · ${inr(f.fare_estimate)}` : "Pick a time"}
                          </Button>
                          <p className="text-xs text-muted">Fare is an estimate: {num(f.road_km, 0)} km{f.route_source !== "osrm" ? " (approx.)" : ""}, {f.truck_tons} t truck, both ways. The transporter confirms by assigning a truck.</p>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {d.fpo && (
                <p className="mt-3 text-sm text-ink2">
                  Or let <b>{d.fpo.name}</b> arrange it:{" "}
                  {d.transport_requested_at
                    ? <span className="text-brand">requested {ago(d.transport_requested_at)}</span>
                    : <button className="underline" disabled={act.busy} onClick={() => post(`/lots/${lotId}/request-transport`)}>ask your FPO</button>}
                </p>
              )}
            </div>
          )}

          {/* 2. the booking: waiting -> confirmed (driver + truck) -> on the way -> handover with the driver's code */}
          {b && !["declined", "cancelled"].includes(b.status) && (
            <div className="rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-semibold">{b.fleet} · pickup {b.pickup_local}</p>
                  <p className="text-sm text-ink2">Fare (est.) {inr(b.fare_estimate)} · {tons(b.tons)} to {b.mandi}</p>
                </div>
                {b.status === "requested" && <Button variant="secondary" disabled={act.busy} onClick={() => post(`/bookings/${b.id}/cancel`)}>Cancel booking</Button>}
              </div>
              {b.status === "requested" && !p && (
                <p className="mt-3 flex items-center gap-2 text-sm text-ink2">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />
                  Waiting for {b.fleet} to confirm and assign a driver…
                </p>
              )}
              {p && (
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <Info label="Confirmed by transporter" value={`✓ ${b.fleet.replace(" (demo)", "")}`} />
                  <Info label="Driver" value={p.driver ?? "–"} sub={p.driver_phone ?? undefined} />
                  <Info label="Truck" value={p.vehicle} sub={`${num(p.capacity_tons, 1)} t`} />
                </div>
              )}
              {p && !p.picked_up && (
                <div className="mt-4 rounded-lg border border-brand/40 bg-brand/5 p-4">
                  <p className="font-semibold">
                    {p.arrived ? `${p.driver ?? "The driver"} has reached your farm` : p.started ? "The truck is on the way to your farm" : "The driver will start the trip soon"}
                  </p>
                  <p className="mt-1 text-sm text-ink2">
                    {p.arrived ? "Ask the driver for the 4-digit pickup code and enter it to hand over your load."
                      : "Follow it on the map. When it arrives, ask the driver for the 4-digit pickup code and enter it here."}
                  </p>
                  {p.started && (
                    <form className="mt-3 flex flex-wrap items-center gap-2"
                      onSubmit={(e) => { e.preventDefault(); post(`/lots/${lotId}/confirm-pickup`, { code }); setCode(""); }}>
                      <input className={`${inputCls} w-32 text-center font-mono text-lg tracking-[0.4em]`} inputMode="numeric" maxLength={4}
                        placeholder="••••" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 4))} aria-label="Pickup code" />
                      <Button type="submit" disabled={act.busy || code.length !== 4}>Confirm pickup</Button>
                      {p.code_tries_left < 5 && <span className="text-xs text-muted">{p.code_tries_left} tries left</span>}
                    </form>
                  )}
                  {p.demo_code && p.started && (
                    <p className="mt-3 text-xs text-muted">Demo: the driver tells you the code <b className="font-mono text-ink">{p.demo_code}</b></p>
                  )}
                </div>
              )}
              {p?.picked_up && !d.done.at_mandi && (
                <p className="mt-3 text-sm"><b className="text-brand">✓ Load handed over</b> · on the way to {b.mandi}. Follow it on the live map.</p>
              )}
            </div>
          )}
          {d.via_fpo && !b && <p className="text-sm">Your FPO has put this lot in a shipment. You&apos;ll get an alert when the truck is on its way.</p>}

          {/* 3. payment */}
          {l?.status === "delivered" && (
            <div className="rounded-xl border border-line p-4">
              <h3 className="mb-2 font-semibold">Payment</h3>
              <p className="text-sm">
                {num(l.delivered_weight_kg, 0)} kg × {inr(l.sale_price_per_quintal)}/quintal = <b className="text-lg">{inr(l.payment?.amount)}</b>
              </p>
              {l.payout_status !== "paid" && <p className="mt-1 text-sm text-ink2">Waiting for the trader to record the payment.</p>}
              {l.payout_status === "paid" && (
                <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
                  <span><b className="text-brand">✓ Paid</b> by {({ bank: "bank transfer", upi: "UPI", cash: "cash", other: "other" } as Record<string, string>)[l.payment?.method] ?? l.payment?.method}
                    {l.payment?.details?.bank_name ? ` to ${l.payment.details.bank_name} a/c ••••${l.payment.details.account_last4}` : ""}
                    {l.payment?.details?.upi_id ? ` to ${l.payment.details.upi_id}` : ""} · ref {l.payment?.reference || "–"} · {dateTime(l.payment?.paid_at)}</span>
                  {l.payment?.received_at
                    ? <span className="rounded-full bg-brand/10 px-2.5 py-1 text-xs font-semibold text-brand">You confirmed receipt {ago(l.payment.received_at)}</span>
                    : <Button disabled={act.busy} onClick={() => post(`/lots/${lotId}/payment-received`)}>I received it</Button>}
                </div>
              )}
              {l.receipt_token && (
                <a href={`/receipt/${l.receipt_token}`} target="_blank" rel="noreferrer"
                  className="mt-3 inline-flex items-center gap-2 rounded-lg border border-brand px-3 py-2 text-sm font-medium text-brand hover:bg-brand hover:text-brand-ink">
                  Delivery receipt {l.receipt_no} (proof for the mandi)
                </a>
              )}
              <p className="mt-2 text-xs text-muted">AgriPulse records the payment; the money moves between you and the trader, not through AgriPulse.</p>
            </div>
          )}
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
