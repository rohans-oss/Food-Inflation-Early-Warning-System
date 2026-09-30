"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { BestMandi } from "@/components/BestMandi";
import { MandiForecast } from "@/components/MandiForecast";
import { NextSteps } from "@/components/NextSteps";
import { QR } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { TripLive } from "@/components/TripLive";
import { Button, Card, ErrorNote, Field, inputCls, SimBadge, Stat, StatusBadge, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, inr, num, tons } from "@/lib/format";
import { useSession } from "@/lib/session";

const STEPS = ["registered", "grouped", "in_transit", "at_mandi", "delivered"];

export default function LotDetail() {
  const { id } = useParams<{ id: string }>();
  const { t } = useSession();
  const lot = useApi<any>(`/lots/${id}`, { poll: 15000 });
  const hist = useApi<any[]>(`/lots/${id}/history`, { poll: 30000 });
  const [forecastMandi, setForecastMandi] = useState<number | null>(null);
  const [lenders, setLenders] = useState<{ id: number; name: string }[]>([]);
  const share = useAction();
  const choose = useAction();
  const [copied, setCopied] = useState(false);

  useEffect(() => { api("/orgs/directory", { query: { kind: "lender" } }).then(setLenders).catch(() => {}); }, []);

  const l = lot.data;
  const stepIdx = l ? STEPS.indexOf(l.status) : -1;

  return (
    <Shell roles={["farmer"]} title={`Lot #${id}`}>
      <Link href="/farmer" className="text-sm underline">← {t("myLots")}</Link>
      <ErrorNote error={lot.error} />
      {l && (
        <>
          <ol className="flex flex-wrap gap-2 text-xs" aria-label="Lot progress">
            {STEPS.map((s, i) => (
              <li key={s} className={`rounded-full border px-3 py-1 ${i <= stepIdx ? "border-brand bg-brand text-brand-ink" : "border-line text-muted"}`}>
                {i + 1}. {s.replace("_", " ")}
              </li>
            ))}
          </ol>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label={t("quantityTons")} value={tons(l.quantity_tons)} sub={`${t("grade")}: ${l.grade}`} />
            <Stat label={t("status")} value={<StatusBadge s={l.status} />} sub={l.pickup_label || undefined} />
            <Stat label={t("mandi")} value={<span className="text-lg">{l.mandi ?? l.preferred_mandi ?? "Not chosen yet"}</span>}
              sub={l.mandi ? "shipment booked" : l.preferred_mandi ? "your choice · your FPO confirms it when grouping" : "pick one below with Sell here"} />
            <Stat label={t("payout")} value={<StatusBadge s={l.payout_status} />} />
          </div>

          {l.status === "delivered" && (
            <Card title={`${t("delivery")} ✓`}>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label={t("weight")} value={`${num(l.delivered_weight_kg, 0)} kg`} sub={`declared ${tons(l.quantity_tons)}`} />
                <Stat label={t("price")} value={inr(l.sale_price_per_quintal)} sub="per quintal" />
                <Stat label="Value" value={inr((l.delivered_weight_kg / 100) * l.sale_price_per_quintal)} />
                <Stat label="Delivered" value={<span className="text-base">{dateTime(l.delivered_at)}</span>} />
              </div>
            </Card>
          )}

          {l.trip && (
            <Card title={t("liveVehicle")} action={<SimBadge on={l.trip.is_simulated} />}>
              {l.trip.pickup_qr_token && (
                <div className="mb-4 flex flex-wrap items-center gap-4 rounded-xl border border-line p-3">
                  <QR value={l.trip.pickup_qr_token} caption="Pickup QR" />
                  <p className="max-w-xs text-sm">{t("pickupQr")}. It proves the driver collected this lot.</p>
                </div>
              )}
              {l.trip.share_url && (
                <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-ink2">{t("trackingLink")}:</span>
                  <code className="max-w-full truncate rounded bg-page px-2 py-1 text-xs">{l.trip.share_url}</code>
                  <Button variant="secondary" onClick={() => { navigator.clipboard?.writeText(l.trip.share_url); setCopied(true); setTimeout(() => setCopied(false), 2000); }}>
                    {copied ? "Copied" : "Copy"}
                  </Button>
                </div>
              )}
              <TripLive tripId={l.trip.id} />
            </Card>
          )}

          {!l.trip && ["registered", "grouped"].includes(l.status) && (
            <Card title={t("bestMandi")}>
              <ErrorNote error={choose.error} />
              <BestMandi lotId={l.id} onPick={setForecastMandi} chosenId={l.mandi_id ?? l.preferred_mandi_id}
                onChoose={(mid) => choose.run(async () => {
                  await api(`/lots/${l.id}/preferred-mandi`, { method: "POST", body: { mandi_id: mid } });
                  lot.reload();
                })}
                chooseLocked={l.status !== "registered" ? "Already grouped into a shipment" : null} />
              {l.status === "registered" && !l.fpo_org_id && (
                <p className="mt-2 text-xs text-muted">Your FPO groups lots into a shipment and books the vehicle. This lot isn't linked to an FPO.</p>
              )}
            </Card>
          )}
          {!l.trip && ["registered", "grouped"].includes(l.status) && (
            <NextSteps key={`${l.preferred_mandi_id ?? 0}-${l.shipment_id ?? 0}`} lotId={l.id} tonsLot={l.quantity_tons} onChanged={lot.reload} />
          )}
          {forecastMandi && <Card title={t("priceForecast")}><MandiForecast mandiId={forecastMandi} /></Card>}

          <div className="grid gap-4 md:grid-cols-2">
            <Card title={t("shareWithLender")}>
              <Field label="Lender" hint="The lender sees this lot's pickup, route, delivery weight and price, nothing else.">
                <select className={inputCls} value={l.lender_org_id ?? ""} disabled={share.busy}
                  onChange={(e) => share.run(async () => {
                    await api(`/lots/${l.id}/lender`, { method: "POST", body: { lender_org_id: e.target.value ? Number(e.target.value) : null } });
                    lot.reload();
                  })}>
                  <option value="">Not shared</option>
                  {lenders.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
              </Field>
              <ErrorNote error={share.error} />
            </Card>
            <Card title={t("history")}>
              <ol className="space-y-1 text-sm">
                {hist.data?.map((h, i) => (
                  <li key={i}><span className="text-muted">{dateTime(h.at)}</span> · {h.entity} {h.field === "payout_status" ? "payout" : ""} → <b>{h.to.replace("_", " ")}</b></li>
                ))}
              </ol>
            </Card>
          </div>
        </>
      )}
    </Shell>
  );
}
