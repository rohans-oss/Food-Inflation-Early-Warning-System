"use client";

import { useCallback, useEffect, useState } from "react";
import PricePanel from "@/components/PricePanel";
import Shell from "@/components/Shell";
import TripLive from "@/components/TripLive";
import { Btn, Card, Empty, ErrorNote, Field, inputCls, QR, SimBadge, Status, SyntheticBadge, Table } from "@/components/ui";
import { api, type Lot, type User } from "@/lib/api";
import { dateTime, num, pct, rs } from "@/lib/format";
import { t, type Lang } from "@/lib/i18n";
import { usePoll } from "@/lib/live";

interface Rec {
  rank: number;
  mandi_id: number;
  mandi: string;
  district: string;
  road_km: number;
  drive_hours: number;
  route_source: string;
  price_forecast: { p10: number; p50: number; p90: number; weeks: number };
  spike_prob_14d: number | null;
  transport_cost: number;
  spoilage_pct: number;
  temp_c: number | null;
  net_value: { p10: number; p50: number; p90: number };
  trained_on_synthetic: boolean;
  clearly_better_than_next: boolean;
}

const KOLAR = { lat: 13.1367, lon: 78.1292 }; // default map centre for the demo region

export default function FarmerPage() {
  return <Shell roles={["farmer"]} title="Farmer">{(u) => <Farmer user={u} />}</Shell>;
}

function Farmer({ user }: { user: User }) {
  const lang = user.preferred_lang as Lang;
  const [lots, setLots] = useState<Lot[] | null>(null);
  const [selId, setSelId] = useState<number | null>(null);
  const load = useCallback(() => api<Lot[]>("/lots").then((l) => { setLots(l); setSelId((s) => s ?? l[0]?.id ?? null); }).catch(() => setLots([])), []);
  usePoll(load, 20000);
  const sel = lots?.find((l) => l.id === selId) || null;

  return (
    <div className="space-y-4">
      <NewLot lang={lang} onCreated={(l) => { setSelId(l.id); load(); }} />
      <Card title={t(lang, "myLots")}>
        {!lots?.length ? (
          <Empty>{t(lang, "noLots")}</Empty>
        ) : (
          <Table head={["Lot", "Tons", "Grade", "Pickup", "Status", "Mandi", "Vehicle", "Payout"]}>
            {lots.map((l) => (
              <tr key={l.id} onClick={() => setSelId(l.id)} className={`cursor-pointer ${l.id === selId ? "bg-green-50" : "hover:bg-slate-50"}`}>
                <td className="px-2 py-1.5">#{l.id} {l.crop}<SimBadge on={l.is_simulated} /></td>
                <td className="px-2 py-1.5">{num(l.quantity_tons, 2)}</td>
                <td className="px-2 py-1.5">{l.grade}</td>
                <td className="px-2 py-1.5">{l.pickup_label || `${l.pickup_lat.toFixed(3)}, ${l.pickup_lon.toFixed(3)}`}</td>
                <td className="px-2 py-1.5"><Status s={l.status} /></td>
                <td className="px-2 py-1.5">{l.mandi || "–"}</td>
                <td className="px-2 py-1.5">{l.trip ? <>{l.trip.vehicle}<SimBadge on={l.trip.is_simulated} /></> : "–"}</td>
                <td className="px-2 py-1.5"><Status s={l.status === "delivered" ? l.payout_status : null} /></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {sel && <LotDetail lot={sel} lang={lang} onChange={load} />}
      <PricePanel near={sel ? { lat: sel.pickup_lat, lon: sel.pickup_lon } : KOLAR} title={t(lang, "todaysPrices")} />
    </div>
  );
}

function NewLot({ lang, onCreated }: { lang: Lang; onCreated: (l: Lot) => void }) {
  const [fpos, setFpos] = useState<{ id: number; name: string }[]>([]);
  const [f, setF] = useState({ quantity_tons: "2", grade: "Local", pickup_label: "", lat: String(KOLAR.lat), lon: String(KOLAR.lon), fpo_org_id: "" });
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { api<{ id: number; name: string }[]>("/orgs/directory?kind=fpo").then(setFpos).catch(() => undefined); }, []);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  const locate = () =>
    navigator.geolocation?.getCurrentPosition(
      (p) => setF((x) => ({ ...x, lat: p.coords.latitude.toFixed(5), lon: p.coords.longitude.toFixed(5) })),
      () => setError("Could not read your location; type it instead."),
      { enableHighAccuracy: true, timeout: 10000 },
    );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const lot = await api<Lot>("/lots", {
        body: {
          crop: "Tomato", quantity_tons: Number(f.quantity_tons), grade: f.grade, pickup_label: f.pickup_label,
          pickup_lat: Number(f.lat), pickup_lon: Number(f.lon), fpo_org_id: f.fpo_org_id ? Number(f.fpo_org_id) : null,
        },
      });
      setOpen(false);
      onCreated(lot);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!open) return <Btn onClick={() => setOpen(true)}>+ {t(lang, "newLot")}</Btn>;
  return (
    <Card title={t(lang, "newLot")}>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-4">
        <Field label={t(lang, "crop")}><input className={inputCls} value="Tomato" disabled /></Field>
        <Field label={t(lang, "quantity")}><input className={inputCls} type="number" step="0.1" min="0.1" max="60" value={f.quantity_tons} onChange={set("quantity_tons")} required /></Field>
        <Field label={t(lang, "grade")}>
          <select className={inputCls} value={f.grade} onChange={set("grade")}>
            {["Local", "FAQ", "Hybrid", "Grade A", "Grade B"].map((g) => <option key={g}>{g}</option>)}
          </select>
        </Field>
        <Field label="FPO (optional)">
          <select className={inputCls} value={f.fpo_org_id} onChange={set("fpo_org_id")}>
            <option value="">Selling on my own</option>
            {fpos.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </Field>
        <Field label={t(lang, "pickup")}><input className={inputCls} placeholder="e.g. Tejas farm, Mulbagal road" value={f.pickup_label} onChange={set("pickup_label")} /></Field>
        <Field label="Latitude"><input className={inputCls} value={f.lat} onChange={set("lat")} required /></Field>
        <Field label="Longitude"><input className={inputCls} value={f.lon} onChange={set("lon")} required /></Field>
        <div className="flex items-end gap-2">
          <Btn type="button" variant="secondary" onClick={locate}>{t(lang, "useMyLocation")}</Btn>
          <Btn type="submit">{t(lang, "save")}</Btn>
        </div>
      </form>
      <ErrorNote error={error} />
      <p className="mt-2 text-xs text-slate-500">V1 covers tomato only. If you pick an FPO, it can group your lot into a shared truck.</p>
    </Card>
  );
}

function LotDetail({ lot, lang, onChange }: { lot: Lot; lang: Lang; onChange: () => void }) {
  return (
    <div className="space-y-4">
      {lot.status === "registered" && <BestMandi lot={lot} lang={lang} />}
      {lot.trip?.pickup_qr_token && (
        <Card title="Pickup QR: show this to the driver when loading">
          <div className="flex flex-wrap items-center gap-6">
            <QR value={lot.trip.pickup_qr_token} />
            <p className="max-w-md text-sm text-slate-600">
              The driver scans this code with the AgriPulse driver app. The scan records who picked up your lot and when, which is the first link in the
              chain of custody your lender can later check.
            </p>
          </div>
        </Card>
      )}
      {lot.trip && <TripLive tripId={lot.trip.id} />}
      {lot.status === "delivered" && (
        <Card title={`✅ ${t(lang, "delivered")}: lot #${lot.id}`}>
          <div className="grid gap-2 text-sm sm:grid-cols-4">
            <div><div className="text-slate-500">Mandi</div><div className="font-medium">{lot.mandi}</div></div>
            <div><div className="text-slate-500">Weighed</div><div className="font-medium">{num(lot.delivered_weight_kg, 0)} kg</div></div>
            <div><div className="text-slate-500">Price</div><div className="font-medium">{rs(lot.sale_price_per_quintal)}/quintal</div></div>
            <div>
              <div className="text-slate-500">Value · payout</div>
              <div className="font-medium">
                {lot.delivered_weight_kg && lot.sale_price_per_quintal ? rs((lot.delivered_weight_kg / 100) * lot.sale_price_per_quintal) : "–"} · <Status s={lot.payout_status} />
              </div>
            </div>
          </div>
          <p className="mt-2 text-xs text-slate-500">Delivered {dateTime(lot.delivered_at)}</p>
        </Card>
      )}
      <LenderShare lot={lot} onChange={onChange} />
    </div>
  );
}

function BestMandi({ lot, lang }: { lot: Lot; lang: Lang }) {
  const [weeks, setWeeks] = useState(1);
  const [rec, setRec] = useState<{ ranked: Rec[]; formula: string; no_forecast: unknown[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setRec(null);
    api<typeof rec>(`/recommend/best-mandi?lot_id=${lot.id}&weeks=${weeks}`).then(setRec).catch((e) => setError(e.message));
  }, [lot.id, weeks]);

  return (
    <Card
      title={`${t(lang, "bestMandi")} #${lot.id} (${num(lot.quantity_tons, 2)} t)`}
      right={
        <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}>
          {[1, 2, 3, 4].map((w) => <option key={w} value={w}>Sell in {w} week{w > 1 ? "s" : ""}</option>)}
        </select>
      }
    >
      <ErrorNote error={error} />
      {!rec ? (
        <Empty>Working out net value per mandi…</Empty>
      ) : !rec.ranked.length ? (
        <Empty>No mandi with a forecast within range yet.</Empty>
      ) : (
        <>
          <Table head={["#", "Mandi", t(lang, "distance"), "Forecast price (p10–p50–p90)", "Transport", "Spoilage", t(lang, "netValue") + " (p10–p50–p90)", t(lang, "spikeRisk")]}>
            {rec.ranked.slice(0, 6).map((r) => (
              <tr key={r.mandi_id} className={r.rank === 1 ? "bg-green-50" : ""}>
                <td className="px-2 py-1.5 font-semibold">{r.rank}</td>
                <td className="px-2 py-1.5">{r.mandi}<SyntheticBadge on={r.trained_on_synthetic} /></td>
                <td className="px-2 py-1.5">{num(r.road_km, 0)} km · {num(r.drive_hours, 1)} h{r.route_source !== "osrm" && <span className="text-xs text-slate-500"> (est.)</span>}</td>
                <td className="px-2 py-1.5 text-xs">{rs(r.price_forecast.p10)}–<b>{rs(r.price_forecast.p50)}</b>–{rs(r.price_forecast.p90)}</td>
                <td className="px-2 py-1.5">{rs(r.transport_cost)}</td>
                <td className="px-2 py-1.5">{num(r.spoilage_pct, 1)}%</td>
                <td className="px-2 py-1.5 text-xs">{rs(r.net_value.p10)}–<b className="text-sm">{rs(r.net_value.p50)}</b>–{rs(r.net_value.p90)}</td>
                <td className="px-2 py-1.5">{pct(r.spike_prob_14d)}</td>
              </tr>
            ))}
          </Table>
          <p className="mt-2 text-xs text-slate-600">
            {rec.ranked[0].clearly_better_than_next
              ? `${rec.ranked[0].mandi} is better than the next option even in a bad week (its p10 beats their p90).`
              : "The top options' ranges overlap, so the ranking is not decisive. Pick on distance or your trader relationship."}
          </p>
          <p className="mt-1 text-xs text-slate-500">{rec.formula}. Your FPO or you book the vehicle.</p>
        </>
      )}
    </Card>
  );
}

function LenderShare({ lot, onChange }: { lot: Lot; onChange: () => void }) {
  const [lenders, setLenders] = useState<{ id: number; name: string }[]>([]);
  useEffect(() => { api<{ id: number; name: string }[]>("/orgs/directory?kind=lender").then(setLenders).catch(() => undefined); }, []);
  const share = async (id: number | null) => {
    await api(`/lots/${lot.id}/lender`, { body: { lender_org_id: id } });
    onChange();
  };
  if (!lenders.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      <span>Share this lot&apos;s verified record with a lender:</span>
      <select className="rounded border border-slate-300 px-2 py-1" value={lot.lender_org_id ?? ""} onChange={(e) => share(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Not shared</option>
        {lenders.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
    </div>
  );
}
