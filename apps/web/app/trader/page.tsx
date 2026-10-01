"use client";

import { useMemo, useState } from "react";

import { MapMarker, MapView } from "@/components/MapView";
import { QRScanner } from "@/components/QR";
import { PaymentForm } from "@/components/PaymentForm";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, inputCls, Note, ProvenanceBadge, SimBadge, Stat, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { inr, num, time, tons } from "@/lib/format";
import { useLiveFeed } from "@/lib/live";
import { useSession } from "@/lib/session";

const PAY_LABEL: Record<string, string> = { bank: "Bank transfer", upi: "UPI", cash: "Cash", other: "Other" };

export default function Trader() {
  const { t } = useSession();
  const board = useApi<any>("/trader/board", { poll: 20000 });
  const [live, setLive] = useState<Record<number, any>>({});
  const [scanFor, setScanFor] = useState<number | null>(null);
  const [weigh, setWeigh] = useState<Record<number, { kg: string; price: string }>>({});
  const [paying, setPaying] = useState<{ lot_id: number; farmer: string; amount: number } | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const act = useAction();

  useLiveFeed((m) => { if (m.type === "position" && m.trip_id) setLive((s) => ({ ...s, [m.trip_id]: m })); });

  const b = board.data;
  const incoming: any[] = (b?.incoming ?? []).map((i: any) => ({ ...i, ...(live[i.trip_id] ? {
    lat: live[i.trip_id].lat, lon: live[i.trip_id].lon, eta_at: live[i.trip_id].eta_at, remaining_km: live[i.trip_id].remaining_km } : {}) }));
  const markers = useMemo<MapMarker[]>(() => incoming.filter((i) => i.lat != null).map((i) => ({
    id: i.trip_id, lat: i.lat, lon: i.lon, kind: "vehicle", label: i.vehicle,
    popup: `${i.vehicle} · ${i.tons} t · ETA ${time(i.eta_at)}`,
  })), [incoming]);

  // Gate: scan whichever truck is here (the driver's delivery QR identifies the trip), then weigh, then pay.
  const [gate, setGate] = useState<any | null>(null);
  const [gateErr, setGateErr] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const scanned = async (code: string) => {
    setGateErr(null);
    try {
      const r = await api<any>("/trader/scan", { method: "POST", body: { token: code } });
      setGate(r);
      setScanning(false);
      setScanFor(null);
      setOk(r.first_scan ? `✓ ${r.vehicle} has arrived. Weigh the load below.` : `${r.vehicle} was already checked in. Weigh the load below.`);
      board.reload();
    } catch (e: any) { setGateErr(e.message); }
  };
  const record = (lot: { lot_id: number; farmer: string }) => act.run(async () => {
    const w = weigh[lot.lot_id];
    await api(`/trader/lots/${lot.lot_id}/weigh`, { method: "POST", body: { weight_kg: Number(w.kg), price_per_quintal: Number(w.price) } });
    setOk(`Lot #${lot.lot_id} weighed: ${num(Number(w.kg), 0)} kg at ${inr(Number(w.price))}/quintal. Now record how ${lot.farmer} is paid.`);
    setPaying({ lot_id: lot.lot_id, farmer: lot.farmer, amount: (Number(w.kg) / 100) * Number(w.price) });
    setGate((g: any) => g && { ...g, lots: g.lots.filter((x: any) => x.lot_id !== lot.lot_id) });
    board.reload();
    setTimeout(() => document.getElementById("pay-form")?.scrollIntoView({ behavior: "smooth", block: "center" }), 300);
  });
  const weighRow = (l: any) => (
    <tr key={l.lot_id}>
      <Td>#{l.lot_id}{l.vehicle && <div className="text-xs text-muted">{l.vehicle}</div>}</Td>
      <Td>{l.farmer}{l.farmer_phone && <div className="text-xs"><a className="underline" href={`tel:${l.farmer_phone}`}>{l.farmer_phone}</a></div>}</Td>
      <Td>{l.crop} · {tons(l.declared_tons)}{l.grade ? ` · ${l.grade}` : ""}</Td>
      <Td><input aria-label="Weight in kg" className={`${inputCls} w-28`} type="number" min="1" placeholder={String(Math.round(l.declared_tons * 1000))}
        value={weigh[l.lot_id]?.kg ?? ""} onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { price: weigh[l.lot_id]?.price ?? "", kg: e.target.value } })} /></Td>
      <Td><input aria-label="Price per quintal" className={`${inputCls} w-28`} type="number" min="1" placeholder={l.price_hint ? String(l.price_hint.modal) : ""}
        value={weigh[l.lot_id]?.price ?? ""} onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { kg: weigh[l.lot_id]?.kg ?? "", price: e.target.value } })} />
        {l.price_hint && <div className="mt-1 text-[11px] text-muted">today {inr(l.price_hint.modal)} <ProvenanceBadge p={l.price_hint.data_provenance} compact /></div>}</Td>
      <Td><Button disabled={!weigh[l.lot_id]?.kg || !weigh[l.lot_id]?.price || act.busy} onClick={() => record(l)}>Weigh &amp; continue to payment</Button></Td>
    </tr>
  );

  const s = b?.summary;
  const ratio = s?.expected_vs_normal;

  return (
    <Shell roles={["trader"]} title={b ? `Incoming supply · ${b.mandi.name}` : "Incoming supply"} wide>
      <ErrorNote error={board.error ?? act.error} />
      {ok && <p role="status" className="rounded-lg border border-good/50 px-3 py-2 text-sm">{ok}</p>}
      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label={t("inTransit")} value={tons(s.tons_in_transit)}  />
          <Stat label={t("expectedToday")} value={tons(s.expected_today_tons_total)} sub={`incl. ${tons(s.confirmed_today_tons)} already weighed`} />
          <Stat label={t("typicalDay")} value={s.typical_daily_tons != null ? tons(s.typical_daily_tons) : "–"}
            sub={<span className="inline-flex items-center gap-1">median of last 28 days <ProvenanceBadge p={s.typical_provenance} compact /></span>} />
          {/* V3-3: zero TRACKED supply is not zero supply - don't turn "nothing tracked" into a price warning */}
          {!s.trucks && !s.confirmed_today_tons ? (
            <Stat label="Expected vs normal" value="–" sub="no tracked or weighed arrivals yet — not a supply signal" />
          ) : (
            <Stat label="Expected vs normal" value={ratio != null ? `${num(ratio * 100, 0)}%` : "–"}
              sub={ratio != null ? `${ratio < 0.7 ? "below" : ratio > 1.3 ? "above" : "near"} normal (tracked + weighed only)` : "no arrival history yet"} />
          )}
        </div>
      )}
      {s?.note && <Note>{s.note}</Note>}

      <Card title="Truck at the gate">
        {!scanning && !scanFor && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => { setScanning(true); setGateErr(null); }}>📷 Scan the driver&apos;s delivery QR</Button>
            <p className="text-sm text-ink2">Ask the driver to open the trip in the AgriPulse Driver app and show the <b>Delivery QR</b>.</p>
          </div>
        )}
        {(scanning || scanFor) && (
          <div className="space-y-2">
            <QRScanner onCode={scanned} label="Scan" autoStart />
            <button className="text-sm underline" onClick={() => { setScanning(false); setScanFor(null); }}>Close scanner</button>
          </div>
        )}
        {gateErr && <p className="mt-3 rounded-lg border border-critical/40 px-3 py-2 text-sm text-critical">{gateErr}</p>}
        {gate && gate.lots.some((x: any) => x.status === "at_mandi") && (
          <div className="mt-4">
            <p className="mb-2 font-semibold">✓ {gate.vehicle}{gate.driver ? ` · ${gate.driver}` : ""} · weigh the load</p>
            <Table head={["Lot", "Farmer", "Produce", "Weight (kg)", "Price (₹/quintal)", ""]}>
              {gate.lots.filter((x: any) => x.status === "at_mandi").map(weighRow)}
            </Table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card title={t("incoming")}>
          <Table head={["Vehicle", "Tons", "Status", t("eta"), t("remaining"), ""]} empty="No vehicles heading here right now.">
            {incoming.map((i) => (
              <tr key={i.trip_id}>
                <Td>{i.vehicle} <SimBadge on={i.is_simulated} />
                  {(i.driver || i.farmers?.length > 0) && <div className="text-xs text-ink2">{[i.driver, i.farmers?.join(", ")].filter(Boolean).join(" · ")}</div>}
                  {!i.pickup_scanned && <div className="text-xs text-muted">not loaded yet</div>}</Td>
                <Td>{num(i.tons, 1)}</Td>
                <Td><StatusBadge s={i.status} /></Td>
                <Td>{time(i.eta_at)}</Td>
                <Td>{i.remaining_km != null ? `${num(i.remaining_km, 0)} km` : "–"}</Td>
                <Td>{i.arrived ? <span className="text-sm font-semibold text-good">✓ At the gate</span>
                  : i.status === "in_progress" && !i.is_simulated ? (
                  <Button variant="secondary" onClick={() => { setScanFor(i.trip_id); window.scrollTo({ top: 0, behavior: "smooth" }); }}>{t("scanDelivery")}</Button>)
                  : i.status === "accepted" ? <span className="text-xs text-muted">driver hasn&apos;t started</span> : null}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Map"><MapView height="h-80" markers={markers} fitKey={markers.length} /></Card>
      </div>

      <Card title={t("awaitingWeighing")}>
        <Table head={["Lot", "Farmer", "Produce", "Weight (kg)", "Price (₹/quintal)", ""]} empty="Nothing waiting at the gate. Scan a truck's delivery QR when it arrives.">
          {b?.awaiting_weighing?.filter((l: any) => !gate?.lots.some((x: any) => x.lot_id === l.lot_id && x.status === "at_mandi")).map(weighRow)}
        </Table>
      </Card>

      <Card title="Weighed in the last 24 hours">
        <Table head={["Lot", "Farmer", "Weight", "Price", "Value", "Payment to farmer"]} empty="None yet today.">
          {b?.delivered_last_24h?.map((l: any) => (
            <tr key={l.lot_id}><Td>#{l.lot_id}{l.receipt_token && <div><a className="text-xs underline" href={`/receipt/${l.receipt_token}`} target="_blank" rel="noreferrer">receipt</a></div>}</Td><Td>{l.farmer}</Td><Td>{num(l.kg, 0)} kg</Td><Td>{inr(l.price_per_quintal)}/q</Td>
              <Td>{inr((l.kg / 100) * l.price_per_quintal)}</Td>
              <Td>
                {l.payout_status === "paid" ? (
                  <span className="text-sm"><b className="text-good">✓ Paid</b> · {PAY_LABEL[l.payment_method] ?? l.payment_method}
                    {l.payment_details?.bank_name ? ` · ${l.payment_details.bank_name} ••${l.payment_details.account_last4}` : ""}
                    {l.payment_details?.upi_id ? ` · ${l.payment_details.upi_id}` : ""}{l.payment_ref ? ` · ref ${l.payment_ref}` : ""}</span>
                ) : (
                  <Button variant={paying?.lot_id === l.lot_id ? "secondary" : "primary"}
                    onClick={() => setPaying(paying?.lot_id === l.lot_id ? null : { lot_id: l.lot_id, farmer: l.farmer, amount: (l.kg / 100) * l.price_per_quintal })}>
                    {paying?.lot_id === l.lot_id ? "Close" : "Record payment"}
                  </Button>
                )}
              </Td></tr>
          ))}
        </Table>
        {paying && <div id="pay-form"><PaymentForm lot={paying} onCancel={() => setPaying(null)}
          onDone={() => { setOk(`Payment to ${paying.farmer} recorded. The farmer has been notified.`); setPaying(null); board.reload(); }} /></div>}
        <p className="mt-2 text-xs text-muted">Recording a payment tells the farmer how and when they were paid. The money itself moves outside AgriPulse.</p>
        <p className="mt-2 text-xs text-muted">Weighed tonnage is added to this mandi&apos;s arrivals as <Badge>trader confirmed</Badge>, kept separate from Agmarknet figures.</p>
      </Card>
    </Shell>
  );
}
