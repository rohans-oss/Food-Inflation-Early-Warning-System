"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import Shell from "@/components/Shell";
import { Card, Empty, ErrorNote, Stat, SyntheticBadge, Table } from "@/components/ui";
import { api, type Horizon } from "@/lib/api";
import { day, num, pct, rs } from "@/lib/format";
import { usePoll } from "@/lib/live";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

interface MandiRow {
  mandi_id: number; mandi: string; district: string; state: string; lat: number; lon: number;
  latest_price: number | null; price_date: string | null; trend_4w_pct: number | null; spike_prob_14d: number | null;
  forecast_2w: Horizon | null; trained_on_synthetic: boolean | null; tons_in_transit: number; arrival_anomaly_7d: number | null; arrivals_synthetic: boolean;
}
interface District { state: string; district: string; mandis: number; max_spike_prob: number | null; tons_in_transit: number; avg_price: number | null; avg_trend_4w_pct: number | null }
interface Overview { mandis: MandiRow[]; districts: District[]; notes: string[] }

const STATES = ["", "Karnataka", "Tamil Nadu", "Andhra Pradesh", "Telangana", "Maharashtra", "Kerala"];

function riskColor(p: number | null) {
  if (p == null) return "#94a3b8";
  if (p >= 0.5) return "#dc2626";
  if (p >= 0.25) return "#f59e0b";
  return "#16a34a";
}

export default function PolicyPage() {
  return <Shell roles={["policy"]} title="Policy analyst / government">{() => <Policy />}</Shell>;
}

function Policy() {
  const [state, setState] = useState("Karnataka");
  const [ov, setOv] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<"spike" | "trend" | "anomaly">("spike");

  const load = () => api<Overview>(`/policy/overview${state ? `?state=${encodeURIComponent(state)}` : ""}`).then(setOv).catch((e) => setError(e.message));
  usePoll(load, 120000, [state]);
  useEffect(() => setOv(null), [state]);

  const markers = useMemo(() => (ov?.mandis || []).map((m) => ({
    id: m.mandi_id, lat: m.lat, lon: m.lon, kind: "mandi" as const, color: riskColor(m.spike_prob_14d),
    label: `${m.mandi.replace(/ APMC$/, "")} ${m.spike_prob_14d != null ? pct(m.spike_prob_14d) : ""}`,
  })), [ov]);

  const rows = useMemo(() => {
    const r = [...(ov?.mandis || [])];
    const k = { spike: (m: MandiRow) => -(m.spike_prob_14d ?? -1), trend: (m: MandiRow) => -(m.trend_4w_pct ?? -999), anomaly: (m: MandiRow) => m.arrival_anomaly_7d ?? 999 }[sortKey];
    return r.sort((a, b) => k(a) - k(b));
  }, [ov, sortKey]);

  const high = ov?.mandis.filter((m) => (m.spike_prob_14d ?? 0) >= 0.5).length ?? 0;
  const synthetic = ov?.mandis.some((m) => m.trained_on_synthetic);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        State:
        <select className="rounded border border-slate-300 px-2 py-1" value={state} onChange={(e) => setState(e.target.value)}>
          {STATES.map((s) => <option key={s} value={s}>{s || "All"}</option>)}
        </select>
        <SyntheticBadge on={synthetic} />
      </div>
      <ErrorNote error={error} />
      {!ov ? <Empty>Loading…</Empty> : (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Mandis monitored" value={ov.mandis.length} />
            <Stat label="High spike risk (≥50%)" value={high} />
            <Stat label="Tons in transit (tracked)" value={num(ov.mandis.reduce((s, m) => s + m.tons_in_transit, 0), 1)} />
            <Stat label="Mandis with arrivals ≥30% below normal" value={ov.mandis.filter((m) => (m.arrival_anomaly_7d ?? 0) <= -0.3).length} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
            <Card title="Spike probability (next 14 days) by mandi">
              <MapView markers={markers} fitKey={`${state}-${markers.length}`} height={420} />
              <p className="mt-1 text-xs text-slate-500">Red ≥ 50%, amber ≥ 25%, green below, grey = no forecast.</p>
            </Card>
            <Card title="Districts, ranked by highest spike risk">
              <Table head={["District", "Mandis", "Avg price", "4-wk trend", "Max spike", "In transit"]}>
                {ov.districts.map((d) => (
                  <tr key={`${d.state}-${d.district}`}>
                    <td className="px-2 py-1.5">{d.district}<div className="text-xs text-slate-500">{d.state}</div></td>
                    <td className="px-2 py-1.5">{d.mandis}</td>
                    <td className="px-2 py-1.5">{rs(d.avg_price)}</td>
                    <td className={`px-2 py-1.5 ${(d.avg_trend_4w_pct ?? 0) > 0 ? "text-red-700" : "text-green-700"}`}>{d.avg_trend_4w_pct == null ? "–" : `${d.avg_trend_4w_pct > 0 ? "+" : ""}${d.avg_trend_4w_pct}%`}</td>
                    <td className="px-2 py-1.5 font-medium" style={{ color: riskColor(d.max_spike_prob) }}>{pct(d.max_spike_prob)}</td>
                    <td className="px-2 py-1.5">{num(d.tons_in_transit, 1)} t</td>
                  </tr>
                ))}
              </Table>
            </Card>
          </div>
          <Card
            title="Mandis"
            right={
              <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={sortKey} onChange={(e) => setSortKey(e.target.value as typeof sortKey)}>
                <option value="spike">Sort: spike risk</option>
                <option value="trend">Sort: 4-week rise</option>
                <option value="anomaly">Sort: arrivals below normal</option>
              </select>
            }
          >
            <Table head={["Mandi", "Latest", "4-wk trend", "2-wk forecast (p10–p50–p90)", "Spike 14d", "Arrivals vs normal (7d)", "In transit"]}>
              {rows.map((m) => (
                <tr key={m.mandi_id}>
                  <td className="px-2 py-1.5">{m.mandi}<div className="text-xs text-slate-500">{m.district}</div></td>
                  <td className="px-2 py-1.5">{rs(m.latest_price)}<div className="text-xs text-slate-500">{day(m.price_date)}</div></td>
                  <td className="px-2 py-1.5">{m.trend_4w_pct == null ? "–" : `${m.trend_4w_pct > 0 ? "+" : ""}${m.trend_4w_pct}%`}</td>
                  <td className="px-2 py-1.5 text-xs">{m.forecast_2w ? <>{rs(m.forecast_2w.p10)}–<b>{rs(m.forecast_2w.p50)}</b>–{rs(m.forecast_2w.p90)}</> : "–"}</td>
                  <td className="px-2 py-1.5 font-medium" style={{ color: riskColor(m.spike_prob_14d) }}>{pct(m.spike_prob_14d)}</td>
                  <td className={`px-2 py-1.5 ${(m.arrival_anomaly_7d ?? 0) <= -0.3 ? "font-semibold text-red-700" : ""}`}>
                    {m.arrival_anomaly_7d == null ? "–" : `${m.arrival_anomaly_7d > 0 ? "+" : ""}${Math.round(m.arrival_anomaly_7d * 100)}%`}
                    <SyntheticBadge on={m.arrivals_synthetic && m.arrival_anomaly_7d != null} />
                  </td>
                  <td className="px-2 py-1.5">{num(m.tons_in_transit, 1)} t</td>
                </tr>
              ))}
            </Table>
            <ul className="mt-2 list-disc pl-5 text-xs text-slate-500">{ov.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </Card>
        </>
      )}
    </div>
  );
}
