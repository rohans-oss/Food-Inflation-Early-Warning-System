"use client";

import { cloneElement, isValidElement, ReactNode, useCallback, useEffect, useId, useState } from "react";

import { api } from "@/lib/api";
import { spikeLevel } from "@/lib/format";

export function Card({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-surface p-4 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl border border-line bg-surface p-4">
      <div className="text-sm text-ink2">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

type BadgeKind = "neutral" | "sim" | "good" | "warn" | "serious" | "critical" | "brand";
const badgeCls: Record<BadgeKind, string> = {
  neutral: "border-line text-ink2",
  sim: "border-amber-400/60 bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
  good: "border-good/50 text-ink",
  warn: "border-warn/60 text-ink",
  serious: "border-serious/60 text-ink",
  critical: "border-critical/60 text-ink",
  brand: "border-brand/50 text-ink",
};
const dotCls: Partial<Record<BadgeKind, string>> = {
  good: "bg-good", warn: "bg-warn", serious: "bg-serious", critical: "bg-critical", brand: "bg-brand",
};

export function Badge({ kind = "neutral", children }: { kind?: BadgeKind; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${badgeCls[kind]}`}>
      {dotCls[kind] && <span aria-hidden className={`h-2 w-2 rounded-full ${dotCls[kind]}`} />}
      {children}
    </span>
  );
}

export type Provenance = "real" | "real_partial" | "synthetic";
const PROV: Record<Provenance, { full: string; short: string; cls: string; dot: string }> = {
  synthetic: {
    full: "SYNTHETIC — METHODOLOGY DEMO",
    short: "SYNTHETIC",
    cls: "border-critical bg-critical/10 text-critical font-semibold",
    dot: "bg-critical",
  },
  real_partial: {
    full: "REAL — LIMITED HISTORY",
    short: "REAL — LIMITED",
    cls: "border-warn bg-warn/15 text-ink font-semibold",
    dot: "bg-warn",
  },
  real: { full: "REAL", short: "REAL", cls: "border-good bg-good/10 text-ink font-semibold", dot: "bg-good" },
};
const RANK: Record<Provenance, number> = { real: 0, real_partial: 1, synthetic: 2 };

/** Worst provenance of a set (one synthetic input makes the whole thing synthetic). */
export function worstProvenance(ps: (string | null | undefined)[]): Provenance | null {
  const v = ps.filter((p): p is Provenance => !!p && p in RANK);
  return v.length ? v.reduce((a, b) => (RANK[b] > RANK[a] ? b : a)) : null;
}

/** V2 rule 9: every forecast / backtest number is shown with where it comes from. Text, never colour alone. */
export function ProvenanceBadge({ p, compact = false }: { p: string | null | undefined; compact?: boolean }) {
  if (!p || !(p in PROV)) return null;
  const d = PROV[p as Provenance];
  const title = p === "synthetic" ? "SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT" : d.full;
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${d.cls}`}>
      <span aria-hidden className={`h-2 w-2 rounded-full ${d.dot}`} />
      {compact ? d.short : d.full}
    </span>
  );
}

/** V3-2 rule 22: every scenario-simulator output. As visible as the SYNTHETIC badge; the full text, never abbreviated. */
export function CounterfactualBadge({ text = "COUNTERFACTUAL ESTIMATE — not a validated causal model" }: { text?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border-2 border-dashed border-warn bg-warn/10 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-ink">
      <span aria-hidden>⚠</span>{text}
    </span>
  );
}

/** Pre-V3 B-1: whether the p10-p90 range shown is calibrated on the model's own track record. Text, not colour. */
export function CalibrationBadge({ c, title }: { c: string; title?: string }) {
  const text: Record<string, string> = {
    applied: "Range calibrated", not_yet_applicable: "Range not yet calibrated", partial: "Range partly calibrated",
    none: "Range uncalibrated", not_needed: "Baseline range",
  };
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${c === "applied" ? "border-good/60 text-ink" : "border-line text-ink2"}`}>
      {text[c] ?? c}
    </span>
  );
}

/** V2 rule 13: trade-flow graph edges are ESTIMATES, and say so in text. */
export function EstimateBadge({ title }: { title?: string }) {
  return (
    <span title={title ?? "ESTIMATE — relative trade-flow index, not measured tonnes"}
      className="inline-flex items-center whitespace-nowrap rounded-full border border-dashed border-ink2 px-2 py-0.5 text-xs font-semibold text-ink2">
      ESTIMATE
    </span>
  );
}

/** Rule 1: anything simulated says so. */
export function SimBadge({ on, label = "Simulated" }: { on: boolean | null | undefined; label?: string }) {
  return on ? <Badge kind="sim">{label}</Badge> : null;
}

/** Spike risk: status colour + icon dot + text label, never colour alone. */
export function SpikeBadge({ p }: { p: number | null | undefined }) {
  const s = spikeLevel(p);
  if (s.level === "none") return <Badge>{s.label}</Badge>;
  return <Badge kind={s.level}>{s.label} · {Math.round((p ?? 0) * 100)}%</Badge>;
}

const statusKind: Record<string, BadgeKind> = {
  registered: "neutral", grouped: "brand", planned: "neutral", booked: "brand", assigned: "neutral",
  accepted: "brand", in_progress: "warn", in_transit: "warn", at_mandi: "serious", delivered: "good",
  completed: "good", paid: "good", pending: "neutral", declined: "critical", cancelled: "critical",
  success: "good", failed: "critical", running: "warn", fresh: "good", stale: "critical", never: "neutral",
  ready: "good", collecting: "warn", stalled: "critical", too_many_gaps: "serious", no_data: "neutral",
};
export function StatusBadge({ s }: { s: string | null | undefined }) {
  if (!s) return null;
  return <Badge kind={statusKind[s] ?? "neutral"}>{s.replace(/_/g, " ")}</Badge>;
}

export function Button({ children, variant = "primary", className = "", ...p }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const v = {
    primary: "bg-brand text-brand-ink hover:opacity-90",
    secondary: "border border-line bg-surface hover:bg-page",
    danger: "bg-critical text-white hover:opacity-90",
    ghost: "text-ink2 hover:text-ink underline-offset-2 hover:underline",
  }[variant];
  return (
    <button {...p} className={`rounded-lg px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${v} ${className}`}>
      {children}
    </button>
  );
}

/** Label linked by id (not wrapping), so a <select>'s accessible name is the label, not its option text. */
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const auto = useId();
  const child = isValidElement<{ id?: string; "aria-describedby"?: string }>(children) ? children : null;
  const id = child?.props.id ?? auto;
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="block text-sm">
      <label htmlFor={id} className="mb-1 block text-ink2">{label}</label>
      {child ? cloneElement(child, { id, "aria-describedby": hintId }) : children}
      {hint && <span id={hintId} className="mt-1 block text-xs text-muted">{hint}</span>}
    </div>
  );
}

export const inputCls = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-brand/40";

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: string }) {
  const hasRows = Array.isArray(children) ? children.flat().filter(Boolean).length > 0 : !!children;
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[520px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            {head.map((h, i) => <th key={i} className="py-2 pr-3 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="tnum">{children}</tbody>
      </table>
      {!hasRows && <p className="py-4 text-sm text-muted">{empty ?? "Nothing here yet."}</p>}
    </div>
  );
}
export const Td = ({ children, className = "" }: { children?: ReactNode; className?: string }) =>
  <td className={`border-b border-line py-2 pr-3 align-top ${className}`}>{children}</td>;

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <p role="alert" className="rounded-lg border border-critical/40 px-3 py-2 text-sm text-critical">{error}</p>;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-line bg-page px-3 py-2 text-xs text-ink2">{children}</p>;
}

/** Fetch helper with loading / error / reload and optional polling. */
export function useApi<T>(path: string | null, opts: { query?: Record<string, unknown>; poll?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const key = path ? path + JSON.stringify(opts.query ?? {}) : null;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path, { query: opts.query });
      setData(d);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    setLoading(!!path);
    load();
    if (!opts.poll) return;
    const id = setInterval(load, opts.poll);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, opts.poll]);

  return { data, error, loading, reload: load, setData };
}

/** Run an action with a busy flag and an error message. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e: any) {
      setError(e.message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
