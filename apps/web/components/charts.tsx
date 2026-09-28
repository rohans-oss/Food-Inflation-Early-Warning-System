"use client";

import type { ForecastBlock } from "@/lib/api";
import { day, pct, rs } from "@/lib/format";
import { SyntheticBadge } from "./ui";

/** Rule 3: a forecast is a range (p10–p50–p90) plus a spike probability, never one number. */
export function ForecastRanges({ fc, latest }: { fc: ForecastBlock | null | undefined; latest?: number | null }) {
  if (!fc) return <p className="text-sm text-slate-500">No forecast yet for this mandi. The nightly job writes one after training.</p>;
  const lo = Math.min(...fc.horizons.map((h) => h.p10), latest ?? Infinity);
  const hi = Math.max(...fc.horizons.map((h) => h.p90), latest ?? -Infinity);
  const span = Math.max(hi - lo, 1);
  const x = (v: number) => `${((v - lo) / span) * 100}%`;
  const spike = fc.spike_prob_14d;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>Issued {day(fc.issue_date)} · {fc.model} · Rs/quintal</span>
        <SyntheticBadge on={fc.trained_on_synthetic} />
        {spike !== null && (
          <span className={`rounded px-1.5 py-0.5 font-semibold ${spike >= 0.5 ? "bg-red-100 text-red-800" : spike >= 0.25 ? "bg-yellow-100 text-yellow-800" : "bg-green-100 text-green-800"}`}>
            Spike risk 14d: {pct(spike)}
          </span>
        )}
      </div>
      {fc.horizons.map((h) => (
        <div key={h.weeks} className="grid grid-cols-[64px_1fr_170px] items-center gap-2 text-sm">
          <span className="text-slate-600">+{h.weeks} wk</span>
          <div className="relative h-5 rounded bg-slate-100">
            <div className="absolute top-0 h-5 rounded bg-green-200" style={{ left: x(h.p10), width: `calc(${x(h.p90)} - ${x(h.p10)})` }} />
            <div className="absolute top-0 h-5 w-0.5 bg-green-800" style={{ left: x(h.p50) }} />
            {latest != null && <div className="absolute -top-0.5 h-6 w-0.5 bg-slate-500" style={{ left: x(latest) }} title="Latest price" />}
          </div>
          <span className="whitespace-nowrap text-xs text-slate-700">
            {rs(h.p10)} – <b>{rs(h.p50)}</b> – {rs(h.p90)}
          </span>
        </div>
      ))}
      <p className="text-xs text-slate-500">Bar = 80% range (p10–p90), dark line = median{latest != null ? ", grey line = latest price" : ""}.</p>
    </div>
  );
}

export function PriceChart({ rows, height = 160 }: { rows: { date: string; modal_price: number; is_synthetic?: boolean }[]; height?: number }) {
  if (rows.length < 2) return <p className="text-sm text-slate-500">Not enough price history yet.</p>;
  const w = 600;
  const vals = rows.map((r) => r.modal_price);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.1 || 1;
  const y = (v: number) => height - 20 - ((v - lo + pad) / (hi - lo + 2 * pad)) * (height - 30);
  const xs = (i: number) => (i / (rows.length - 1)) * (w - 50) + 45;
  const d = rows.map((r, i) => `${i ? "L" : "M"}${xs(i).toFixed(1)},${y(r.modal_price).toFixed(1)}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${height}`} className="w-full" role="img" aria-label="Modal price history">
        {[lo, (lo + hi) / 2, hi].map((v) => (
          <g key={v}>
            <line x1={45} x2={w - 5} y1={y(v)} y2={y(v)} stroke="#e2e8f0" />
            <text x={40} y={y(v) + 4} textAnchor="end" fontSize="10" fill="#64748b">{Math.round(v)}</text>
          </g>
        ))}
        <path d={d} fill="none" stroke="#15803d" strokeWidth={2} />
        <text x={45} y={height - 4} fontSize="10" fill="#64748b">{day(rows[0].date)}</text>
        <text x={w - 5} y={height - 4} fontSize="10" fill="#64748b" textAnchor="end">{day(rows[rows.length - 1].date)}</text>
      </svg>
      {rows.some((r) => r.is_synthetic) && <SyntheticBadge on />}
    </div>
  );
}
