"use client";

import { useEffect, useState } from "react";
import { api, type ForecastBlock, type LatestPrice } from "@/lib/api";
import { day, num, perKg, rs } from "@/lib/format";
import { ForecastRanges, PriceChart } from "./charts";
import { Card, Empty, ErrorNote, SyntheticBadge, Table } from "./ui";

/** Latest mandi prices (optionally near a point) + forecast range and history for the selected mandi. */
export default function PricePanel({ near, title = "Today's tomato prices", initialMandiId }: { near?: { lat: number; lon: number } | null; title?: string; initialMandiId?: number | null }) {
  const [rows, setRows] = useState<LatestPrice[] | null>(null);
  const [sel, setSel] = useState<number | null>(initialMandiId ?? null);
  const [fc, setFc] = useState<ForecastBlock | null>(null);
  const [hist, setHist] = useState<{ date: string; modal_price: number; is_synthetic: boolean }[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = near ? `?near_lat=${near.lat}&near_lon=${near.lon}&radius_km=300` : "";
    api<LatestPrice[]>(`/prices/latest${q}`)
      .then((r) => {
        setRows(r);
        if (r.length && sel === null) setSel(r[0].mandi.id);
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [near?.lat, near?.lon]);

  useEffect(() => {
    if (sel === null) return;
    setFc(null);
    api<ForecastBlock>(`/forecasts/${sel}`).then(setFc).catch(() => setFc(null));
    api<typeof hist>(`/prices/history?mandi_id=${sel}&days=180`).then(setHist).catch(() => setHist([]));
  }, [sel]);

  const selected = rows?.find((r) => r.mandi.id === sel);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={title}>
        <ErrorNote error={error} />
        {rows === null ? (
          <Empty>Loading…</Empty>
        ) : rows.length === 0 ? (
          <Empty>No prices yet. The daily Agmarknet job fills this in (Admin → data freshness).</Empty>
        ) : (
          <Table head={["Mandi", "Date", "Modal", "Min–max", near ? "Distance" : "District"]}>
            {rows.slice(0, 15).map((r) => (
              <tr key={r.mandi.id} onClick={() => setSel(r.mandi.id)} className={`cursor-pointer ${sel === r.mandi.id ? "bg-green-50" : "hover:bg-slate-50"}`}>
                <td className="px-2 py-1.5">{r.mandi.name}<SyntheticBadge on={r.is_synthetic} /></td>
                <td className="px-2 py-1.5 text-slate-600">{day(r.date)}</td>
                <td className="px-2 py-1.5 font-medium">{rs(r.modal_price)} <span className="text-xs text-slate-500">{perKg(r.modal_price)}</span></td>
                <td className="px-2 py-1.5 text-slate-600">{rs(r.min_price)}–{rs(r.max_price)}</td>
                <td className="px-2 py-1.5 text-slate-600">{near ? `${num(r.distance_km, 0)} km` : r.mandi.district}</td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-2 text-xs text-slate-500">Source: Agmarknet via data.gov.in, updated daily. Prices in Rs/quintal (100 kg).</p>
      </Card>
      <Card title={selected ? `${selected.mandi.name}: forecast` : "Forecast"}>
        <ForecastRanges fc={fc} latest={selected?.modal_price} />
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Last 6 months (modal price)</div>
          <PriceChart rows={hist} />
        </div>
      </Card>
    </div>
  );
}
