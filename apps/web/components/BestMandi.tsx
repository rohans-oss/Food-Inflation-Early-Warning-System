"use client";

import { useState } from "react";

import { inr, num } from "@/lib/format";
import { useSession } from "@/lib/session";

import { Badge, ErrorNote, Note, SpikeBadge, Table, Td, useApi } from "./ui";

/** Rule-based best mandi (V1). Ranked by p50 net value; p10-p90 shown so overlap is visible. */
export function BestMandi({ lotId, onPick }: { lotId: number; onPick?: (mandiId: number) => void }) {
  const { t } = useSession();
  const [weeks, setWeeks] = useState(1);
  const r = useApi<any>("/recommend/best-mandi", { query: { lot_id: lotId, weeks } });
  const ranked: any[] = r.data?.ranked ?? [];
  const synthetic = ranked.some((x) => x.trained_on_synthetic);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-ink2">Selling in</span>
        {[1, 2, 3, 4].map((w) => (
          <button key={w} onClick={() => setWeeks(w)}
            className={`rounded-md border px-2 py-1 ${w === weeks ? "border-brand bg-brand text-brand-ink" : "border-line"}`}>
            {w} wk
          </button>
        ))}
        {synthetic && <Badge kind="sim">{t("synthetic")}</Badge>}
      </div>
      <ErrorNote error={r.error} />
      <Table head={["#", t("mandi"), "Road", `${t("price")} p50 (p10–p90)`, t("transport"), t("spoilage"), `${t("netValue")} p50 (p10–p90)`, t("spikeRisk")]}
        empty={r.loading ? t("loading") : "No mandis with a forecast nearby."}>
        {ranked.map((m) => (
          <tr key={m.mandi_id} className={m.rank === 1 ? "bg-page" : ""}>
            <Td>{m.rank}</Td>
            <Td>
              <div className="font-medium">{m.mandi}</div>
              <div className="text-xs text-muted">{m.district}{!m.coords_verified && " · location unverified"}</div>
              {onPick && <button onClick={() => onPick(m.mandi_id)} className="text-xs underline">forecast</button>}
            </Td>
            <Td>{num(m.road_km, 0)} km<div className="text-xs text-muted">{num(m.drive_hours, 1)} h{m.route_source !== "osrm" && " · approx."}</div></Td>
            <Td>{inr(m.price_forecast.p50)}<div className="text-xs text-muted">{inr(m.price_forecast.p10)}–{inr(m.price_forecast.p90)}/q</div></Td>
            <Td>−{inr(m.transport_cost)}</Td>
            <Td>−{num(m.spoilage_pct, 1)}%<div className="text-xs text-muted">{num(m.temp_c, 0)}°C{m.temp_source === "default" && " (assumed)"}</div></Td>
            <Td><b>{inr(m.net_value.p50)}</b><div className="text-xs text-muted">{inr(m.net_value.p10)}–{inr(m.net_value.p90)}</div></Td>
            <Td><SpikeBadge p={m.spike_prob_14d} /></Td>
          </tr>
        ))}
      </Table>
      {ranked.length > 1 && !ranked[0].clearly_better_than_next && (
        <Note>The top two ranges overlap, so the ranking is not decisive: #{2} could pay as much. Weigh distance and reliability too.</Note>
      )}
      {r.data?.no_forecast?.length > 0 && <p className="text-xs text-muted">Nearby but no forecast yet: {r.data.no_forecast.join(", ")}.</p>}
      {r.data && <p className="text-xs text-muted">{r.data.formula}. Cost assumptions: ₹{r.data.inputs.rate_per_km_ton}/km/t, from {r.data.inputs.config_file}.</p>}
    </div>
  );
}
