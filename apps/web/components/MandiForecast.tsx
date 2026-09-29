"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { day } from "@/lib/format";
import { useSession } from "@/lib/session";

import { ForecastBlock, ForecastChart, HistoryPoint } from "./ForecastChart";
import { Badge, SpikeBadge } from "./ui";

/** Price history + model forecast + naive baseline for one mandi. */
export function MandiForecast({ mandiId, name }: { mandiId: number; name?: string }) {
  const { t } = useSession();
  const [hist, setHist] = useState<HistoryPoint[]>([]);
  const [fc, setFc] = useState<(ForecastBlock & { model_version?: string }) | null>(null);
  const [base, setBase] = useState<ForecastBlock | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setMsg(null);
    api<HistoryPoint[]>("/prices/history", { query: { mandi_id: mandiId, days: 120 } }).then(setHist).catch(() => setHist([]));
    api<ForecastBlock>(`/forecasts/${mandiId}`).then(setFc).catch((e) => { setFc(null); setMsg(e.message); });
    api<ForecastBlock>("/forecasts/baseline", { query: { mandi_id: mandiId } }).then(setBase).catch(() => setBase(null));
  }, [mandiId]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {name && <b>{name}</b>}
        {fc && <span className="text-ink2">issued {day(fc.issue_date)}</span>}
        {fc && <><span className="text-ink2">{t("spikeRisk")}:</span><SpikeBadge p={fc.spike_prob_14d} /></>}
        {fc?.trained_on_synthetic && <Badge kind="sim">{t("synthetic")}</Badge>}
      </div>
      {msg && !fc && <p className="text-sm text-muted">{msg}</p>}
      <ForecastChart history={hist} forecast={fc} baseline={base} />
      {fc?.trained_on_synthetic && <p className="text-xs text-muted">{t("syntheticNote")}</p>}
    </div>
  );
}
