const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const inr = (v: number | null | undefined) => (v == null ? "–" : "₹" + inr0.format(Math.round(v)));
export const num = (v: number | null | undefined, d = 1) =>
  v == null ? "–" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: d }).format(v);
export const tons = (v: number | null | undefined) => (v == null ? "–" : `${num(v, 1)} t`);
export const pct = (v: number | null | undefined, d = 0) => (v == null ? "–" : `${num(v * 100, d)}%`);
export const signedPct = (v: number | null | undefined) =>
  v == null ? "–" : `${v > 0 ? "+" : ""}${num(v, 1)}%`;

export function time(v: string | null | undefined) {
  if (!v) return "–";
  return new Date(v).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" });
}
export function dateTime(v: string | null | undefined) {
  if (!v) return "–";
  return new Date(v).toLocaleString("en-IN", {
    day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata",
  });
}
export function day(v: string | null | undefined) {
  if (!v) return "–";
  return new Date(v).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
export function ago(v: string | null | undefined) {
  if (!v) return "never";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/** Spike probability -> status level (reserved status colours, always shown with a label). */
export function spikeLevel(p: number | null | undefined): { level: "good" | "warn" | "serious" | "critical" | "none"; label: string } {
  if (p == null) return { level: "none", label: "No forecast" };
  if (p >= 0.6) return { level: "critical", label: "High" };
  if (p >= 0.4) return { level: "serious", label: "Elevated" };
  if (p >= 0.2) return { level: "warn", label: "Watch" };
  return { level: "good", label: "Low" };
}
