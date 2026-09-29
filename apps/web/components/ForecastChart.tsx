"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { inr } from "@/lib/format";

import { ProvenanceBadge, worstProvenance } from "./ui";

export interface Horizon { weeks: number; target_date?: string; p10: number; p50: number; p90: number }
export interface ForecastBlock { issue_date: string; horizons: Horizon[]; trained_on_synthetic?: boolean; spike_prob_14d?: number; data_provenance?: string }
export interface HistoryPoint { date: string; modal_price: number }

const H = 250, PAD = { l: 56, r: 16, t: 12, b: 28 };
const DAY = 86400000;

/**
 * One y-axis (Rs/quintal). History = solid series-1 line; model = series-1 band (p10-p90) + p50 line;
 * naive baseline = series-2 dashed p50 + dashed p10/p90 edges. Legend always shown (2 series);
 * hover crosshair snaps to the nearest day that has data; table view below.
 */
export function ForecastChart({ history, forecast, baseline, days = 90 }: {
  history: HistoryPoint[];
  forecast?: ForecastBlock | null;
  baseline?: ForecastBlock | null;
  days?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  // draw at the container's real pixel width so 11px labels stay 11px on a phone.
  // Callback ref: re-attaches when the wrapper element changes (empty state -> chart).
  const [W, setW] = useState(680);
  const ro = useRef<ResizeObserver | null>(null);
  const box = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect();
    if (!el) return;
    ro.current = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))));
    ro.current.observe(el);
  }, []);
  useEffect(() => () => ro.current?.disconnect(), []);

  const g = useMemo(() => {
    const hist = history.map((h) => ({ t: new Date(h.date).getTime(), v: h.modal_price })).sort((a, b) => a.t - b.t);
    const last = hist[hist.length - 1];
    const cut = last ? last.t - days * DAY : 0;
    const shown = hist.filter((p) => p.t >= cut);
    const anchor = forecast ? new Date(forecast.issue_date).getTime() : last?.t ?? Date.now();
    const anchorV = last?.v ?? forecast?.horizons[0]?.p50 ?? 0;
    const pts = (f?: ForecastBlock | null) =>
      f ? [{ t: anchor, p10: anchorV, p50: anchorV, p90: anchorV },
        ...f.horizons.map((h) => ({ t: anchor + h.weeks * 7 * DAY, p10: h.p10, p50: h.p50, p90: h.p90 }))] : [];
    const model = pts(forecast), base = pts(baseline);
    const ts = [...shown.map((p) => p.t), ...model.map((p) => p.t), ...base.map((p) => p.t)];
    const vs = [...shown.map((p) => p.v), ...model.flatMap((p) => [p.p10, p.p90]), ...base.flatMap((p) => [p.p10, p.p90])];
    if (!ts.length) return null;
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    const vmin = Math.min(...vs), vmax = Math.max(...vs);
    const padV = (vmax - vmin) * 0.08 || vmax * 0.1 || 1;
    const y0 = Math.max(0, vmin - padV), y1 = vmax + padV;
    const x = (t: number) => PAD.l + ((t - t0) / Math.max(t1 - t0, 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
    const ticksY = Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4);
    const nx = W < 480 ? 3 : 5;
    const ticksX = Array.from({ length: nx }, (_, i) => t0 + ((t1 - t0) * i) / (nx - 1));
    // hover stops: every history day + every forecast point
    const stops = [...shown.map((p) => ({ t: p.t, hist: p.v })), ...model.slice(1).map((p, i) => ({ t: p.t, model: p, base: base[i + 1] }))];
    return { shown, model, base, x, y, ticksY, ticksX, stops, anchor };
  }, [history, forecast, baseline, days, W]);

  // one label for everything drawn: the worst of model and baseline (synthetic wins)
  const prov = worstProvenance([forecast?.data_provenance, baseline?.data_provenance]);
  if (!g) return <div ref={box}><p className="text-sm text-muted">No price data yet.</p></div>;
  const line = (ps: { t: number; v: number }[]) => ps.map((p, i) => `${i ? "L" : "M"}${g.x(p.t).toFixed(1)},${g.y(p.v).toFixed(1)}`).join("");
  const band = (ps: { t: number; p10: number; p90: number }[]) =>
    ps.length ? line(ps.map((p) => ({ t: p.t, v: p.p90 }))) + ps.slice().reverse().map((p) => `L${g.x(p.t).toFixed(1)},${g.y(p.p10).toFixed(1)}`).join("") + "Z" : "";

  const onMove = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0, bd = Infinity;
    g.stops.forEach((s, i) => { const d = Math.abs(g.x(s.t) - px); if (d < bd) { bd = d; best = i; } });
    setHover(best);
  };
  const h = hover != null ? g.stops[hover] : null;
  const fmtD = (t: number) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  return (
    <div ref={box}>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink2">
        <span className="flex items-center gap-1.5"><svg width="22" height="10" aria-hidden><rect x="0" y="1" width="22" height="8" rx="2" fill="var(--series-1)" opacity=".18" /><line x1="0" x2="22" y1="5" y2="5" stroke="var(--series-1)" strokeWidth="2" /></svg>Model p50 (band p10–p90)</span>
        {baseline && <span className="flex items-center gap-1.5"><svg width="22" height="10" aria-hidden><line x1="0" x2="22" y1="5" y2="5" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="4 3" /></svg>Naive baseline</span>}
        <span className="text-muted">Rs/quintal</span>
        <ProvenanceBadge p={prov} />
        <button onClick={() => setShowTable((s) => !s)} className="ml-auto underline">{showTable ? "Hide table" : "Table view"}</button>
      </div>
      <div className="relative">
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full touch-none select-none" role="img"
          aria-label="Tomato price history and 1 to 4 week forecast range" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {g.ticksY.map((v, i) => (
            <g key={i}>
              <line x1={PAD.l} x2={W - PAD.r} y1={g.y(v)} y2={g.y(v)} stroke="var(--grid)" strokeWidth="1" />
              <text x={PAD.l - 8} y={g.y(v) + 4} textAnchor="end" className="tnum fill-muted text-[11px]">{Math.round(v).toLocaleString("en-IN")}</text>
            </g>
          ))}
          {g.ticksX.map((t, i) => (
            <text key={i} x={g.x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === g.ticksX.length - 1 ? "end" : "middle"} className="fill-muted text-[11px]">{fmtD(t)}</text>
          ))}
          <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--axis)" />
          {g.model.length > 0 && <line x1={g.x(g.anchor)} x2={g.x(g.anchor)} y1={PAD.t} y2={H - PAD.b} stroke="var(--axis)" strokeDasharray="2 3" />}
          <path d={band(g.model)} fill="var(--series-1)" opacity="0.16" />
          {g.base.length > 0 && <>
            <path d={line(g.base.map((p) => ({ t: p.t, v: p.p90 })))} fill="none" stroke="var(--series-2)" strokeWidth="1" strokeDasharray="3 3" opacity=".7" />
            <path d={line(g.base.map((p) => ({ t: p.t, v: p.p10 })))} fill="none" stroke="var(--series-2)" strokeWidth="1" strokeDasharray="3 3" opacity=".7" />
            <path d={line(g.base.map((p) => ({ t: p.t, v: p.p50 })))} fill="none" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="5 4" />
          </>}
          <path d={line(g.shown)} fill="none" stroke="var(--series-1)" strokeWidth="2" strokeLinejoin="round" />
          <path d={line(g.model.map((p) => ({ t: p.t, v: p.p50 })))} fill="none" stroke="var(--series-1)" strokeWidth="2" />
          {g.model.slice(1).map((p) => <circle key={p.t} cx={g.x(p.t)} cy={g.y(p.p50)} r="4" fill="var(--series-1)" stroke="rgb(var(--surface))" strokeWidth="2" />)}
          {h && <line x1={g.x(h.t)} x2={g.x(h.t)} y1={PAD.t} y2={H - PAD.b} stroke="rgb(var(--ink2))" strokeWidth="1" />}
        </svg>
        {h && (
          <div className="pointer-events-none absolute top-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow"
            style={{ left: `${Math.min(70, (g.x(h.t) / W) * 100)}%` }}>
            <div className="mb-1 flex items-center gap-2 text-muted">{fmtD(h.t)} <ProvenanceBadge p={prov} compact /></div>
            {"hist" in h && h.hist != null && <div><b className="tnum">{inr(h.hist)}</b> observed</div>}
            {"model" in h && h.model && <div><b className="tnum">{inr(h.model.p50)}</b> model p50 <span className="text-muted">({inr(h.model.p10)}–{inr(h.model.p90)})</span></div>}
            {"base" in h && h.base && <div><b className="tnum">{inr(h.base.p50)}</b> baseline <span className="text-muted">({inr(h.base.p10)}–{inr(h.base.p90)})</span></div>}
          </div>
        )}
      </div>
      {showTable && forecast && (
        <table className="mt-3 w-full text-sm tnum">
          {prov && <caption className="mb-1 text-left"><ProvenanceBadge p={prov} /></caption>}
          <thead><tr className="text-left text-xs text-muted"><th className="py-1">Horizon</th><th>Model p10</th><th>p50</th><th>p90</th>{baseline && <th>Baseline p50</th>}</tr></thead>
          <tbody>
            {forecast.horizons.map((f, i) => (
              <tr key={f.weeks} className="border-t border-line">
                <td className="py-1">{f.weeks} wk</td><td>{inr(f.p10)}</td><td>{inr(f.p50)}</td><td>{inr(f.p90)}</td>
                {baseline && <td>{inr(baseline.horizons[i]?.p50)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
