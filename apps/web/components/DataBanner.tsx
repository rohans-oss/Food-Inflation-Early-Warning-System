"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/api";

type Status = { mode: "live" | "demo"; price_feed: string; latest_real_price_date: string | null; trucks: string };

/** Top-of-page line saying which data the site shows. Build-time text (NEXT_PUBLIC_DEMO_NOTICE) until the API
 * answers /data-status; then the API's own account wins, so the label can't go stale when the mode changes. */
export function DataBanner({ fallback }: { fallback: string }) {
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => {
    fetch(`${apiBase()}/data-status`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then(setS).catch(() => {});
  }, []);
  let text = fallback;
  if (s?.mode === "demo") text = "Demo site: prices and forecasts are sample data, not live mandi rates. Transporters, drivers and accounts are for demonstration.";
  if (s?.mode === "live") {
    const sim = s.trucks === "simulated" ? " Transporters, drivers and accounts are for demonstration." : "";
    text = s.price_feed === "connected"
      ? `Live data: real mandi prices from Agmarknet${s.latest_real_price_date ? ` (latest ${s.latest_real_price_date})` : ""}.${sim}`
      : s.price_feed === "no_key"
        ? `Live mode: the real price feed (data.gov.in) is not connected yet, so no mandi prices are shown.${sim}`
        : `Live mode: waiting for the first real price update from Agmarknet.${sim}`;
  }
  if (!text) return null;
  return <div role="note" className="no-print bg-ink px-4 py-1.5 text-center text-xs text-page">{text}</div>;
}
