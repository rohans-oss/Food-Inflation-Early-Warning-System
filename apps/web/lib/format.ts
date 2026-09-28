export const rs = (v: number | null | undefined) =>
  v === null || v === undefined ? "–" : "₹" + Math.round(v).toLocaleString("en-IN");

export const num = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits });

export const pct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)}%`;

export function time(v: string | null | undefined): string {
  if (!v) return "–";
  const d = new Date(v.endsWith("Z") || v.includes("+") ? v : v + "Z");
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "–";
  const d = new Date(v.endsWith("Z") || v.includes("+") ? v : v + "Z");
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

export function day(v: string | null | undefined): string {
  if (!v) return "–";
  return new Date(v + (v.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

/** Rs/quintal -> Rs/kg, which is what farmers actually quote. */
export const perKg = (perQuintal: number | null | undefined) =>
  perQuintal === null || perQuintal === undefined ? "–" : `₹${(perQuintal / 100).toFixed(1)}/kg`;
