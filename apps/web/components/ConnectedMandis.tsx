"use client";

import { useState } from "react";

import { EstimateBadge, ErrorNote, Note, ProvenanceBadge, Table, Td, inputCls, useApi } from "@/components/ui";
import { day, num } from "@/lib/format";

type Edge = {
  mandi: { id: number; name: string; district: string };
  edge_type: "distance" | "price_corr" | "flow_estimate";
  edge_label: string;
  direction: "both" | "in" | "out";
  km: number | null;
  distance_source: string | null;
  correlation: number | null;
  flow_index: number | null;
  data_provenance: string;
  is_estimate: boolean;
  estimate_label: string | null;
};

function detail(e: Edge) {
  if (e.edge_type === "distance") return `${num(e.km, 0)} km by road${e.distance_source === "haversine" ? " (approx.)" : ""}`;
  if (e.edge_type === "price_corr") return `weekly price changes correlate ${num(e.correlation, 2)}`;
  const idx = `index ${num(e.flow_index, 2)} of 1`;
  return e.direction === "in" ? `produce likely arrives from here · ${idx}` : `produce likely goes here · ${idx}`;
}

/** V2-3: a mandi's neighbours in the mandi graph. Every row carries its provenance; flow rows say ESTIMATE. */
export function ConnectedMandis({ mandis, initial }: { mandis: { id: number; name: string }[]; initial?: number }) {
  const [id, setId] = useState<number | undefined>(initial ?? mandis[0]?.id);
  const g = useApi<any>(id ? `/graph/mandi/${id}/neighbours` : null);
  const rows: Edge[] = g.data?.neighbours ?? [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Mandi" className={`${inputCls} max-w-xs`} value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
          {mandis.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        {g.data?.build && <span className="text-xs text-muted">graph built {day(g.data.build.as_of)}</span>}
      </div>
      <ErrorNote error={g.error} />
      <Table head={["Mandi", "Link", "Detail", "Source"]} empty={g.data?.build === null ? "Graph not built yet" : "No connected mandis"}>
        {rows.map((e) => (
          <tr key={`${e.edge_type}-${e.direction}-${e.mandi.id}`}>
            <Td>{e.mandi.name}<div className="text-xs text-muted">{e.mandi.district}</div></Td>
            <Td>{e.edge_label}</Td>
            <Td>{detail(e)}</Td>
            <Td><span className="flex flex-wrap gap-1"><ProvenanceBadge p={e.data_provenance} compact />{e.is_estimate && <EstimateBadge title={e.estimate_label ?? undefined} />}</span></Td>
          </tr>
        ))}
      </Table>
      <Note>{(g.data?.notes ?? []).join(" ")}</Note>
    </div>
  );
}
