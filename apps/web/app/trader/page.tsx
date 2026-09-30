"use client";

import { useMemo, useState } from "react";

import { MapMarker, MapView } from "@/components/MapView";
import { QRScanner } from "@/components/QR";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, inputCls, Note, ProvenanceBadge, SimBadge, Stat, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { inr, num, time, tons } from "@/lib/format";
import { useLiveFeed } from "@/lib/live";
import { useSession } from "@/lib/session";

export default function Trader() {
  const { t } = useSession();
  const board = useApi<any>("/trader/board", { poll: 20000 });
  const [live, setLive] = useState<Record<number, any>>({});
  const [scanFor, setScanFor] = useState<number | null>(null);
  const [weigh, setWeigh] = useState<Record<number, { kg: string; price: string }>>({});
  const [ok, setOk] = useState<string | null>(null);
  const act = useAction();

  useLiveFeed((m) => { if (m.type === "position" && m.trip_id) setLive((s) => ({ ...s, [m.trip_id]: m })); });

  const b = board.data;
  const incoming: any[] = (b?.incoming ?? []).map((i: any) => ({ ...i, ...(live[i.trip_id] ? {
    lat: live[i.trip_id].lat, lon: live[i.trip_id].lon, eta_at: live[i.trip_id].eta_at, remaining_km: live[i.trip_id].remaining_km } : {}) }));
  const markers = useMemo<MapMarker[]>(() => incoming.filter((i) => i.lat != null).map((i) => ({
    id: i.trip_id, lat: i.lat, lon: i.lon, kind: i.is_simulated ? "vehicle-sim" : "vehicle", label: i.vehicle,
    popup: `${i.vehicle} · ${i.tons} t · ETA ${time(i.eta_at)}${i.is_simulated ? " (Simulated)" : ""}`,
  })), [incoming]);

  const scanned = (tripId: number) => async (code: string) => {
    await act.run(async () => {
      await api(`/trips/${tripId}/scan/delivery`, { method: "POST", body: { token: code } });
      setScanFor(null);
      setOk(`Arrival confirmed for trip #${tripId}. Weigh the lots below.`);
      board.reload();
    });
  };
  const record = (lotId: number) => act.run(async () => {
    const w = weigh[lotId];
    await api(`/trader/lots/${lotId}/weigh`, { method: "POST", body: { weight_kg: Number(w.kg), price_per_quintal: Number(w.price) } });
    setOk(`Lot #${lotId} recorded. The farmer has been notified.`);
    board.reload();
  });

  const s = b?.summary;
  const ratio = s?.expected_vs_normal;

  return (
    <Shell roles={["trader"]} title={b ? `Incoming supply · ${b.mandi.name}` : "Incoming supply"} wide>
      <ErrorNote error={board.error ?? act.error} />
      {ok && <p role="status" className="rounded-lg border border-good/50 px-3 py-2 text-sm">{ok}</p>}
      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label={t("inTransit")} value={tons(s.tons_in_transit)} sub={s.tons_simulated ? `${num(s.tons_simulated, 1)} t simulated` : undefined} />
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

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card title={t("incoming")}>
          <Table head={["Vehicle", "Tons", "Status", t("eta"), t("remaining"), ""]} empty="No vehicles heading here right now.">
            {incoming.map((i) => (
              <tr key={i.trip_id}>
                <Td>{i.vehicle} <SimBadge on={i.is_simulated} />{!i.pickup_scanned && <div className="text-xs text-muted">not loaded yet</div>}</Td>
                <Td>{num(i.tons, 1)}</Td>
                <Td><StatusBadge s={i.status} /></Td>
                <Td>{time(i.eta_at)}</Td>
                <Td>{i.remaining_km != null ? `${num(i.remaining_km, 0)} km` : "–"}</Td>
                <Td>{i.status === "in_progress" && !i.is_simulated && (
                  <Button variant="secondary" onClick={() => setScanFor(scanFor === i.trip_id ? null : i.trip_id)}>{t("scanDelivery")}</Button>)}</Td>
              </tr>
            ))}
          </Table>
          {scanFor && (
            <div className="mt-3 rounded-xl border border-line p-3">
              <p className="mb-2 text-sm">Scan the <b>delivery QR on the driver&apos;s phone</b> for trip #{scanFor}.</p>
              <QRScanner onCode={scanned(scanFor)} label="Scan" />
            </div>
          )}
        </Card>
        <Card title="Map"><MapView height="h-80" markers={markers} fitKey={markers.length} /></Card>
      </div>

      <Card title={t("awaitingWeighing")}>
        <Table head={["Lot", "Farmer", "Declared", t("grade"), "Weight (kg)", "Price (₹/quintal)", ""]} empty="Nothing waiting at the gate.">
          {b?.awaiting_weighing?.map((l: any) => (
            <tr key={l.lot_id}>
              <Td>#{l.lot_id}</Td><Td>{l.farmer}</Td><Td>{tons(l.declared_tons)}</Td><Td>{l.grade}</Td>
              <Td><input aria-label="Weight in kg" className={`${inputCls} w-28`} type="number" min="1" value={weigh[l.lot_id]?.kg ?? ""}
                onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { price: weigh[l.lot_id]?.price ?? "", kg: e.target.value } })} /></Td>
              <Td><input aria-label="Price per quintal" className={`${inputCls} w-28`} type="number" min="1" value={weigh[l.lot_id]?.price ?? ""}
                onChange={(e) => setWeigh({ ...weigh, [l.lot_id]: { kg: weigh[l.lot_id]?.kg ?? "", price: e.target.value } })} /></Td>
              <Td><Button disabled={!weigh[l.lot_id]?.kg || !weigh[l.lot_id]?.price || act.busy} onClick={() => record(l.lot_id)}>{t("recordWeighing")}</Button></Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Weighed in the last 24 hours">
        <Table head={["Lot", "Farmer", "Weight", "Price", "Value"]} empty="None yet today.">
          {b?.delivered_last_24h?.map((l: any) => (
            <tr key={l.lot_id}><Td>#{l.lot_id}</Td><Td>{l.farmer}</Td><Td>{num(l.kg, 0)} kg</Td><Td>{inr(l.price_per_quintal)}/q</Td>
              <Td>{inr((l.kg / 100) * l.price_per_quintal)}</Td></tr>
          ))}
        </Table>
        <p className="mt-2 text-xs text-muted">Weighed tonnage is added to this mandi&apos;s arrivals as <Badge>trader confirmed</Badge>, kept separate from Agmarknet figures.</p>
      </Card>
    </Shell>
  );
}
