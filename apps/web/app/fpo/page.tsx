"use client";

import { useEffect, useState } from "react";

import { BestMandi } from "@/components/BestMandi";
import { SharedLoadsCard } from "@/components/LoadProposals";
import { QR } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { TripLive } from "@/components/TripLive";
import { Button, Card, ErrorNote, Field, inputCls, SimBadge, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, inr, num, time, tons } from "@/lib/format";
import { useSession } from "@/lib/session";

export default function Fpo() {
  const { t } = useSession();
  const lots = useApi<any[]>("/lots", { query: { status: "registered" }, poll: 30000 });
  const shipments = useApi<any[]>("/shipments", { poll: 20000 });
  const [mandis, setMandis] = useState<any[]>([]);
  const [fleets, setFleets] = useState<{ id: number; name: string }[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [mandiId, setMandiId] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const act = useAction();

  useEffect(() => {
    api("/mandis").then((m: any[]) => setMandis(m.filter((x) => x.lat != null))).catch(() => {});
    api("/orgs/directory", { query: { kind: "fleet" } }).then(setFleets).catch(() => {});
  }, []);

  const toggle = (id: number) => {
    const pref = lots.data?.find((l) => l.id === id)?.preferred_mandi_id;
    if (!picked.has(id) && pref && !mandiId) setMandiId(String(pref)); // start from the farmer's own choice
    setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };
  const pickedTons = (lots.data ?? []).filter((l) => picked.has(l.id)).reduce((a, l) => a + l.quantity_tons, 0);
  const firstPicked = [...picked][0];

  const group = () => act.run(async () => {
    await api("/shipments", { method: "POST", body: { mandi_id: Number(mandiId), lot_ids: [...picked] } });
    setPicked(new Set());
    lots.reload();
    shipments.reload();
  });
  const book = (sid: number, fleet: string) => act.run(async () => {
    await api(`/shipments/${sid}/book`, { method: "POST", body: { fleet_org_id: Number(fleet) } });
    shipments.reload();
  });
  const pay = (lotId: number) => act.run(async () => { await api(`/lots/${lotId}/payout`, { method: "POST" }); shipments.reload(); });

  return (
    <Shell roles={["fpo"]} title="FPO dispatch">
      <ErrorNote error={act.error} />
      <Card title={`Members' lots ready to ship`}>
        <Table head={["", "Lot", "Farmer", t("quantityTons"), t("grade"), t("pickupPoint"), "Farmer's choice"]} empty="No registered lots from your members.">
          {lots.data?.map((l) => (
            <tr key={l.id} className={picked.has(l.id) ? "bg-page" : ""}>
              <Td><input type="checkbox" aria-label={`Select lot ${l.id}`} checked={picked.has(l.id)} onChange={() => toggle(l.id)} /></Td>
              <Td>#{l.id} <SimBadge on={l.is_simulated} /></Td>
              <Td>{l.farmer.name}</Td>
              <Td>{tons(l.quantity_tons)}</Td>
              <Td>{l.grade}</Td>
              <Td className="text-ink2">{l.pickup_label || `${num(l.pickup_lat, 3)}, ${num(l.pickup_lon, 3)}`}</Td>
              <Td>{l.preferred_mandi ?? <span className="text-muted">–</span>}</Td>
            </tr>
          ))}
        </Table>
        {picked.size > 0 && (
          <div className="mt-4 space-y-3 rounded-xl border border-line p-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="text-sm"><b>{picked.size}</b> lots · <b>{tons(pickedTons)}</b></div>
              <Field label={t("mandi")}>
                <select className={inputCls} value={mandiId} onChange={(e) => setMandiId(e.target.value)}>
                  <option value="">Choose…</option>
                  {mandis.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </Field>
              <Button disabled={!mandiId || act.busy} onClick={group}>{t("createShipment")}</Button>
            </div>
            {firstPicked && (
              <details>
                <summary className="cursor-pointer text-sm underline">Suggested mandis for lot #{firstPicked}</summary>
                <div className="mt-2"><BestMandi lotId={firstPicked} /></div>
              </details>
            )}
          </div>
        )}
      </Card>

      <SharedLoadsCard onChanged={() => { lots.reload(); shipments.reload(); }} />

      <Card title={t("shipments")}>
        <div className="space-y-3">
          {shipments.data?.length === 0 && <p className="text-sm text-muted">No shipments yet.</p>}
          {shipments.data?.map((s) => (
            <div key={s.id} className="rounded-xl border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <b>Shipment #{s.id}</b> → {s.mandi} · {tons(s.total_tons)} <StatusBadge s={s.status} />
                <SimBadge on={s.is_simulated} />
                <span className="text-xs text-muted">{dateTime(s.created_at)}</span>
                <button className="ml-auto text-sm underline" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Hide" : "Details"}</button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
                {s.fleet ? <span>Fleet: <b>{s.fleet.name}</b></span> : <span className="text-ink2">No fleet booked</span>}
                {["planned", "booked"].includes(s.status) && !s.trip && (
                  <select aria-label={t("bookFleet")} className={`${inputCls} w-auto`} value="" onChange={(e) => e.target.value && book(s.id, e.target.value)}>
                    <option value="">{s.fleet ? "Change fleet…" : `${t("bookFleet")}…`}</option>
                    {fleets.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                  </select>
                )}
                {s.trip && <span>Vehicle <b>{s.trip.vehicle}</b> <StatusBadge s={s.trip.status} /> {s.trip.eta_at && `ETA ${time(s.trip.eta_at)}`} <SimBadge on={s.trip.is_simulated} /></span>}
                {s.status === "booked" && !s.trip && <span className="text-xs text-muted">Waiting for the fleet owner to assign a vehicle and driver.</span>}
              </div>
              {open === s.id && (
                <div className="mt-3 space-y-3">
                  <h3 className="text-sm font-semibold">{t("farmerTonnage")}</h3>
                  <Table head={["Farmer", "Declared", "Delivered", "Lots"]}>
                    {s.farmers.map((f: any) => (
                      <tr key={f.farmer_id}><Td>{f.farmer}</Td><Td>{tons(f.tons)}</Td><Td>{f.delivered_kg ? `${num(f.delivered_kg, 0)} kg` : "–"}</Td><Td>{f.lots.map((x: number) => `#${x}`).join(", ")}</Td></tr>
                    ))}
                  </Table>
                  <h3 className="text-sm font-semibold">{t("payout")}</h3>
                  <Table head={["Lot", "Farmer", t("status"), t("weight"), t("price"), "Value", t("payout"), ""]}>
                    {s.lots.map((l: any) => (
                      <tr key={l.id}>
                        <Td>#{l.id}</Td><Td>{l.farmer}</Td><Td><StatusBadge s={l.status} /></Td>
                        <Td>{l.delivered_weight_kg ? `${num(l.delivered_weight_kg, 0)} kg` : "–"}</Td>
                        <Td>{inr(l.sale_price_per_quintal)}</Td>
                        <Td>{l.delivered_weight_kg && l.sale_price_per_quintal ? inr((l.delivered_weight_kg / 100) * l.sale_price_per_quintal) : "–"}</Td>
                        <Td><StatusBadge s={l.payout_status} /></Td>
                        <Td>{l.status === "delivered" && l.payout_status === "pending" && <Button variant="secondary" onClick={() => pay(l.id)}>{t("markPaid")}</Button>}</Td>
                      </tr>
                    ))}
                  </Table>
                  {s.trip?.pickup_qr_token && (
                    <div className="flex flex-wrap items-center gap-4 rounded-xl border border-line p-3">
                      <QR value={s.trip.pickup_qr_token} caption="Pickup QR" />
                      <p className="max-w-xs text-sm">Show this to the driver at loading. Each farmer also sees it on their lot page.</p>
                    </div>
                  )}
                  {s.trip && <TripLive tripId={s.trip.id} />}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </Shell>
  );
}
