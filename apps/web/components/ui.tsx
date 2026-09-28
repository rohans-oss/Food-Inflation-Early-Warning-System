"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

export function Card({ title, right, children, className = "" }: { title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold text-slate-800">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

/** Rule 1: anything simulated says so, every time it's shown. */
export function SimBadge({ on, label = "Simulated" }: { on: boolean | null | undefined; label?: string }) {
  if (!on) return null;
  return <span className="ml-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold uppercase text-amber-800">{label}</span>;
}

export const SyntheticBadge = ({ on }: { on: boolean | null | undefined }) => <SimBadge on={on} label="Synthetic data" />;

const STATUS_COLOR: Record<string, string> = {
  registered: "bg-slate-100 text-slate-700",
  grouped: "bg-indigo-100 text-indigo-800",
  planned: "bg-slate-100 text-slate-700",
  booked: "bg-indigo-100 text-indigo-800",
  assigned: "bg-indigo-100 text-indigo-800",
  accepted: "bg-sky-100 text-sky-800",
  in_progress: "bg-blue-100 text-blue-800",
  in_transit: "bg-blue-100 text-blue-800",
  at_mandi: "bg-purple-100 text-purple-800",
  delivered: "bg-green-100 text-green-800",
  completed: "bg-green-100 text-green-800",
  paid: "bg-green-100 text-green-800",
  pending: "bg-yellow-100 text-yellow-800",
  declined: "bg-red-100 text-red-800",
  cancelled: "bg-red-100 text-red-800",
  fresh: "bg-green-100 text-green-800",
  stale: "bg-red-100 text-red-800",
  never: "bg-slate-200 text-slate-700",
  success: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  running: "bg-blue-100 text-blue-800",
};

export function Status({ s }: { s: string | null | undefined }) {
  if (!s) return <span className="text-slate-400">–</span>;
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_COLOR[s] || "bg-slate-100 text-slate-700"}`}>{s.replace(/_/g, " ")}</span>;
}

export function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-4 text-center text-sm text-slate-500">{children}</p>;
}

export function Table({ head, children }: { head: React.ReactNode[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
          <tr>{head.map((h, i) => <th key={i} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Btn({ children, variant = "primary", className = "", ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const v = {
    primary: "bg-green-700 text-white hover:bg-green-800 disabled:bg-green-300",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400",
    danger: "bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300",
  }[variant];
  return <button {...rest} className={`rounded-md px-3 py-1.5 text-sm font-medium ${v} ${className}`}>{children}</button>;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

export const inputCls = "w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm focus:border-green-600 focus:outline-none";

/** QR for the chain of custody. The token is opaque; the driver PWA scans it. */
export function QR({ value, size = 180 }: { value: string; size?: number }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    QRCode.toDataURL(value, { width: size, margin: 1 }).then(setSrc).catch(() => setSrc(""));
  }, [value, size]);
  return (
    <div className="inline-flex flex-col items-center gap-1">
      {src ? <img src={src} width={size} height={size} alt="QR code" /> : <div style={{ width: size, height: size }} />}
      <code className="max-w-[220px] break-all text-[10px] text-slate-500">{value}</code>
    </div>
  );
}
