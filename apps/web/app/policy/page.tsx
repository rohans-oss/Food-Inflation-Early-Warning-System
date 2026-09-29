"use client";

import { useMemo, useState } from "react";

import { MandiForecast } from "@/components/MandiForecast";
import { MapMarker, MapView } from "@/components/MapView";
import { Shell } from "@/components/Shell";
import { Card, ErrorNote, Note, ProvenanceBadge, SpikeBadge, Table, Td, useApi, worstProvenance } from "@/components/ui";
import { inr, num, signedPct, spikeLevel, tons } from "@/lib/format";
import { useSession } from "@/lib/session";

// Reserved status colours (dataviz reference palette); always paired with a text label in the legend, popups and tables.
const STATUS_HEX = { good: "#0ca30c", warn: "#fab219", serious: "#ec835a", critical: "#d03b3b", none: "#898781" } as const;

export default function Policy() {
  const { t } = useSession();
  const [state, setState] = useState("");
  const ov = useApi<any>("/policy/overview", { query: { state }, poll: 60000 });
  const [focus, setFocus] = useState<any>(null);
  const mandis: any[] = ov.data?.mandis ?? [];
  const prov = worstProvenance([...mandis.map((m) => m.data_provenance), ...mandis.map((m) => (m.arrivals_synthetic ? "synthetic" : null))]);

  const markers = useMemo<MapMarker[]>(() => mandis.map((m) => {
    const s = spikeLevel(m.spike_prob_14d);
    // area encodes tonnage in transit (sqrt so area, not radius, is proportional)
    const size = 14 + Math.min(34, Math.sqrt(m.tons_in_transit || 0) * 6);
    return {
      id: m.mandi_id, lat: m.lat, lon: m.lon, kind: "status", color: STATUS_HEX[s.level], size, label: m.mandi,
      popup: `${m.mandi} (${m.district})\nSpike risk: ${s.label}${m.spike_prob_14d != null ? ` ${Math.round(m.spike_prob_14d * 100)}%` : ""}\n` +
        `Price ${inr(m.latest_price)}/q · 4-wk ${signedPct(m.trend_4w_pct)}\nIn transit ${tons(m.tons_in_transit)}`,
    };
  }), [mandis]);

  const states = Array.from(new Set(mandis.map((m) => m.state))).sort();

  return (
    <Shell roles={["policy", "admin"]} title="Tomato price risk monitor" wide>
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="State" className="rounded-md border border-line bg-surface px-2 py-1 text-sm" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">All states</option>
          {states.map((s) => <option key={s}>{s}</option>)}
        </select>
        <ProvenanceBadge p={prov} />
      </div>
      <ErrorNote error={ov.error} />
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card title="Map">
          <MapView height="h-[30rem]" markers={markers} fitKey={`${state}-${markers.length}`} />
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink2">
            <span>Colour = spike risk (14 days):</span>
            {(["good", "warn", "serious", "critical"] as const).map((k) => (
              <span key={k} className="flex items-center gap-1"><span className="h-3 w-3 rounded-full" style={{ background: STATUS_HEX[k] }} />
                {{ good: "Low <20%", warn: "Watch 20–40%", serious: "Elevated 40–60%", critical: "High ≥60%" }[k]}</span>
            ))}
            <span>· Size = tonnes in transit</span>
          </div>
        </Card>
        <Card title={t("districts")} action={<ProvenanceBadge p={prov} compact />}>
          <Table head={["District", "Mandis", "Avg price", t("trend4w"), "Max spike risk", t("inTransit")]}>
            {ov.data?.districts?.map((d: any) => (
              <tr key={`${d.state}-${d.district}`}>
                <Td>{d.district}<div className="text-xs text-muted">{d.state}</div></Td>
                <Td>{d.mandis}</Td><Td>{inr(d.avg_price)}</Td><Td>{signedPct(d.avg_trend_4w_pct)}</Td>
                <Td><SpikeBadge p={d.max_spike_prob} /></Td><Td>{tons(d.tons_in_transit)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
      <Card title="Mandis">
        <Table head={["Mandi", "Latest price", t("trend4w"), "2-wk forecast (p10–p90)", t("spikeRisk"), t("inTransit"), t("arrivalAnomaly")]}>
          {[...mandis].sort((a, b) => (b.spike_prob_14d ?? -1) - (a.spike_prob_14d ?? -1)).map((m) => (
            <tr key={m.mandi_id} className="cursor-pointer hover:bg-page" onClick={() => setFocus(m)}>
              <Td>{m.mandi}<div className="text-xs text-muted">{m.district}</div></Td>
              <Td>{inr(m.latest_price)}</Td>
              <Td>{signedPct(m.trend_4w_pct)}</Td>
              <Td>{m.forecast_2w ? <>{inr(m.forecast_2w.p50)} <span className="text-xs text-muted">({inr(m.forecast_2w.p10)}–{inr(m.forecast_2w.p90)})</span>{" "}<ProvenanceBadge p={m.data_provenance} compact /></> : "–"}</Td>
              <Td><SpikeBadge p={m.spike_prob_14d} /></Td>
              <Td>{tons(m.tons_in_transit)}</Td>
              <Td>{m.arrival_anomaly_7d != null ? `${m.arrival_anomaly_7d > 0 ? "+" : ""}${num(m.arrival_anomaly_7d * 100, 0)}%` : "–"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {focus && <Card title={`${t("priceForecast")} · ${focus.mandi}`}><MandiForecast mandiId={focus.mandi_id} /></Card>}
      <Note>{(ov.data?.notes ?? []).join(" ")}</Note>
    </Shell>
  );
}
