"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { MandiForecast } from "@/components/MandiForecast";
import { MapView } from "@/components/MapView";
import { Shell } from "@/components/Shell";
import { Button, Card, ErrorNote, Field, inputCls, ProvenanceBadge, SimBadge, StatusBadge, Table, Td, useAction, useApi, worstProvenance } from "@/components/ui";
import { api } from "@/lib/api";
import { day, inr, num, time, tons } from "@/lib/format";
import { useSession } from "@/lib/session";

const DEFAULT_FARM: [number, number] = [13.2, 78.02]; // Kolar belt, until the farmer sets a point

export default function Farmer() {
  const { t } = useSession();
  const router = useRouter();
  const lots = useApi<any[]>("/lots", { poll: 30000 });
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [f, setF] = useState({ quantity_tons: "2", grade: "Local", pickup_label: "", fpo_org_id: "", lender_org_id: "" });
  const [fpos, setFpos] = useState<{ id: number; name: string }[]>([]);
  const [lenders, setLenders] = useState<{ id: number; name: string }[]>([]);
  const { busy, error, run, setError } = useAction();
  const [mandi, setMandi] = useState<{ id: number; name: string } | null>(null);

  useEffect(() => {
    api("/orgs/directory", { query: { kind: "fpo" } }).then(setFpos).catch(() => {});
    api("/orgs/directory", { query: { kind: "lender" } }).then(setLenders).catch(() => {});
  }, []);
  // prices near the chosen point, else near the latest lot, else the default
  const last = lots.data?.[0];
  const here: [number, number] = point ?? (last ? [last.pickup_lat, last.pickup_lon] : DEFAULT_FARM);
  const prices = useApi<any[]>("/prices/latest", { query: { near_lat: here[0], near_lon: here[1], radius_km: 150 } });
  useEffect(() => { if (!mandi && prices.data?.length) setMandi(prices.data[0].mandi); }, [prices.data, mandi]);

  const locate = () =>
    navigator.geolocation?.getCurrentPosition(
      (p) => setPoint([p.coords.latitude, p.coords.longitude]),
      (e) => setError(`Location unavailable: ${e.message}`),
      { enableHighAccuracy: true, timeout: 15000 },
    );

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    if (!point) return setError("Set the pickup point: use your location or click the map.");
    run(async () => {
      const lot = await api("/lots", {
        method: "POST",
        body: {
          quantity_tons: Number(f.quantity_tons), grade: f.grade, pickup_label: f.pickup_label,
          pickup_lat: point[0], pickup_lon: point[1],
          fpo_org_id: f.fpo_org_id ? Number(f.fpo_org_id) : null, lender_org_id: f.lender_org_id ? Number(f.lender_org_id) : null,
        },
      });
      router.push(`/farmer/lots/${lot.id}`);
    });
  };

  return (
    <Shell roles={["farmer"]} title={t("myLots")}>
      <Card>
        <Table head={["Lot", t("quantityTons"), t("status"), t("mandi"), t("eta"), ""]} empty="No lots yet. Register your first harvest below.">
          {lots.data?.map((l) => (
            <tr key={l.id}>
              <Td>#{l.id} <span className="text-muted">{day(l.created_at)}</span></Td>
              <Td>{tons(l.quantity_tons)}</Td>
              <Td><StatusBadge s={l.status} /></Td>
              <Td>{l.mandi ?? "–"}</Td>
              <Td>{l.trip?.eta_at ? time(l.trip.eta_at) : "–"} <SimBadge on={l.trip?.is_simulated} /></Td>
              <Td><Link className="underline" href={`/farmer/lots/${l.id}`}>Open</Link></Td>
            </tr>
          ))}
        </Table>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t("newLot")}>
          <form onSubmit={create} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("quantityTons")}>
                <input className={inputCls} type="number" min="0.1" max="60" step="0.1" required value={f.quantity_tons}
                  onChange={(e) => setF({ ...f, quantity_tons: e.target.value })} />
              </Field>
              <Field label={t("grade")}>
                <select className={inputCls} value={f.grade} onChange={(e) => setF({ ...f, grade: e.target.value })}>
                  {["Local", "Small", "Medium", "Large", "FAQ"].map((g) => <option key={g}>{g}</option>)}
                </select>
              </Field>
            </div>
            <Field label={t("pickupLabel")}>
              <input className={inputCls} placeholder="e.g. Tejas farm, Vemagal" value={f.pickup_label} onChange={(e) => setF({ ...f, pickup_label: e.target.value })} />
            </Field>
            <div>
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="text-ink2">{t("pickupPoint")}: {point ? `${num(point[0], 4)}, ${num(point[1], 4)}` : "not set"}</span>
                <Button type="button" variant="secondary" onClick={locate}>{t("useMyLocation")}</Button>
              </div>
              <MapView height="h-56" center={[here[1], here[0]]} zoom={9} fitKey={point ? point.join() : "init"}
                onClick={(lat, lon) => setPoint([lat, lon])}
                markers={point ? [{ id: "p", lat: point[0], lon: point[1], kind: "pickup", label: "Pickup" }] : []} />
              <p className="mt-1 text-xs text-muted">{t("orClickMap")}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("fpo")}>
                <select className={inputCls} value={f.fpo_org_id} onChange={(e) => setF({ ...f, fpo_org_id: e.target.value })}>
                  <option value="">{t("none")}</option>
                  {fpos.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
              </Field>
              <Field label={t("lender")}>
                <select className={inputCls} value={f.lender_org_id} onChange={(e) => setF({ ...f, lender_org_id: e.target.value })}>
                  <option value="">{t("none")}</option>
                  {lenders.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
              </Field>
            </div>
            <ErrorNote error={error} />
            <Button type="submit" disabled={busy}>{t("register")}</Button>
          </form>
        </Card>

        <Card title={t("pricesNearby")} action={<ProvenanceBadge p={worstProvenance((prices.data ?? []).map((p) => p.data_provenance))} />}>
          <ErrorNote error={prices.error} />
          <Table head={[t("mandi"), t("distance"), t("modalPrice"), t("range")]} empty="No mandi prices within 150 km yet.">
            {prices.data?.slice(0, 10).map((p) => (
              <tr key={p.mandi.id} onClick={() => setMandi(p.mandi)}
                className={`cursor-pointer hover:bg-page ${mandi?.id === p.mandi.id ? "bg-page" : ""}`}>
                <Td>{p.mandi.name} {p.data_provenance !== "real" && <ProvenanceBadge p={p.data_provenance} compact />}</Td>
                <Td>{num(p.distance_km, 0)} km</Td>
                <Td><b>{inr(p.modal_price)}</b><span className="text-muted">/q</span></Td>
                <Td className="text-ink2">{inr(p.min_price)}–{inr(p.max_price)} <span className="text-xs text-muted">{day(p.date)}</span></Td>
              </tr>
            ))}
          </Table>
          <p className="mt-2 text-xs text-muted">Rs per quintal (100 kg). Tap a mandi to see its forecast.</p>
        </Card>
      </div>

      {mandi && (
        <Card title={`${t("priceForecast")} · ${mandi.name}`}>
          <MandiForecast mandiId={mandi.id} />
        </Card>
      )}
    </Shell>
  );
}
