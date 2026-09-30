"use client";

import { useState } from "react";

import { inr, num } from "@/lib/format";
import { useSession } from "@/lib/session";

import { ErrorNote, Note, ProvenanceBadge, SpikeBadge, Table, Td, useApi, worstProvenance } from "./ui";

/** Best mandi: the V1 rule or the V3 optimizer (config/recommender.toml). Ranked by p50 net value; p10-p90 shown so
 * overlap is visible. With the optimizer, options that break a hard limit (spoilage, mandi room) are listed last. */
export function BestMandi({ lotId, onPick, chosenId, onChoose, chooseLocked }: {
  lotId: number;
  onPick?: (mandiId: number) => void;
  /** farmer's own choice: the chosen mandi is highlighted and each row gets a "Sell here" button */
  chosenId?: number | null;
  onChoose?: (mandiId: number | null) => void;
  chooseLocked?: string | null;
}) {
  const { t } = useSession();
  const [weeks, setWeeks] = useState(1);
  const r = useApi<any>("/recommend/best-mandi", { query: { lot_id: lotId, weeks } });
  const ranked: any[] = r.data?.ranked ?? [];
  const noPrice = !!r.data?.no_price_forecast;
  const prov = worstProvenance(ranked.map((x) => x.data_provenance));

  return (
    <div className="space-y-3">
      <div className={`flex flex-wrap items-center gap-2 text-sm ${noPrice ? "hidden" : ""}`}>
        <span className="text-ink2">Selling in</span>
        {[1, 2, 3, 4].map((w) => (
          <button key={w} onClick={() => setWeeks(w)}
            className={`rounded-md border px-2 py-1 ${w === weeks ? "border-brand bg-brand text-brand-ink" : "border-line"}`}>
            {w} wk
          </button>
        ))}
        {!noPrice && <ProvenanceBadge p={prov} />}
      </div>
      <ErrorNote error={r.error} />
      {noPrice && (
        <Note>There is no price forecast for {r.data.crop} yet (AgriPulse models tomato prices only), so mandis are ranked by
          transport cost; check today&apos;s rate with the mandi before you sell.</Note>
      )}
      <Table head={noPrice
        ? ["#", t("mandi"), "Road", t("transport"), t("spoilage"), ...(onChoose ? ["Your choice"] : [])]
        : ["#", t("mandi"), "Road", `${t("price")} p50 (p10–p90)`, t("transport"), t("spoilage"), `${t("netValue")} p50 (p10–p90)`, t("spikeRisk"), ...(onChoose ? ["Your choice"] : [])]}
        empty={r.loading ? t("loading") : "No mandis with a forecast nearby."}>
        {ranked.map((m) => (
          <tr key={m.mandi_id} className={m.mandi_id === chosenId ? "bg-brand/10" : m.feasible === false ? "opacity-60" : m.rank === 1 ? "bg-page" : ""}>
            <Td>{m.rank}</Td>
            <Td>
              <div className="font-medium">{m.mandi}</div>
              <div className="text-xs text-muted">{m.district}{!m.coords_verified && " · location unverified"}</div>
              {m.feasible === false && <div className="text-xs text-critical">Not advised: {m.why_not.join("; ")}</div>}
              {onPick && <button onClick={() => onPick(m.mandi_id)} className="text-xs underline">forecast</button>}
            </Td>
            <Td>{num(m.road_km, 0)} km<div className="text-xs text-muted">{num(m.drive_hours, 1)} h{m.route_source !== "osrm" && " · approx."}</div></Td>
            {!noPrice && <Td>{inr(m.price_forecast?.p50)}<div className="text-xs text-muted">{inr(m.price_forecast?.p10)}–{inr(m.price_forecast?.p90)}/q</div></Td>}
            <Td>−{inr(m.transport_cost)}</Td>
            <Td>−{num(m.spoilage_pct, 1)}%<div className="text-xs text-muted">{num(m.temp_c, 0)}°C{m.temp_source === "default" && " (assumed)"}</div></Td>
            {!noPrice && <Td><b>{inr(m.net_value?.p50)}</b><div className="text-xs text-muted">{inr(m.net_value?.p10)}–{inr(m.net_value?.p90)}</div></Td>}
            {!noPrice && <Td><SpikeBadge p={m.spike_prob_14d} /></Td>}
            {onChoose && (
              <Td>
                {m.mandi_id === chosenId ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="whitespace-nowrap rounded-full bg-brand px-2.5 py-1 text-xs font-semibold text-brand-ink">✓ Chosen</span>
                    {!chooseLocked && <button onClick={() => onChoose(null)} className="text-xs text-ink2 underline">clear</button>}
                  </span>
                ) : (
                  <button onClick={() => onChoose(m.mandi_id)} disabled={!!chooseLocked} title={chooseLocked ?? undefined}
                    className="whitespace-nowrap rounded-md border border-brand px-2.5 py-1 text-xs font-medium text-brand hover:bg-brand hover:text-brand-ink disabled:cursor-not-allowed disabled:opacity-40">
                    Sell here
                  </button>
                )}
              </Td>
            )}
          </tr>
        ))}
      </Table>
      {!noPrice && ranked.length > 1 && !ranked[0].clearly_better_than_next && (
        <Note>The top two ranges overlap, so the ranking is not decisive: #{2} could pay as much. Weigh distance and reliability too.</Note>
      )}
      {r.data?.no_forecast?.length > 0 && <p className="text-xs text-muted">Nearby but no forecast yet: {r.data.no_forecast.join(", ")}.</p>}
      {noPrice && <p className="text-xs text-muted">{r.data.formula}.</p>}
      {r.data && !noPrice && r.data.recommender !== "optimizer" && <p className="text-xs text-muted">{r.data.formula}. Cost assumptions: ₹{r.data.inputs.rate_per_km_ton}/km/t, from {r.data.inputs.config_file}.</p>}
      {r.data?.recommender === "optimizer" && (
        <p className="text-xs text-muted">{r.data.formula}. Assumes a {r.data.vehicle_assumption.capacity_tons} t truck hired at your farm,
          ₹{num(r.data.vehicle_assumption.rate_per_km, 0)}/km{r.data.vehicle_assumption.return_leg ? ", paid both ways" : ""} (from {r.data.inputs.config_file}).</p>
      )}
    </div>
  );
}
