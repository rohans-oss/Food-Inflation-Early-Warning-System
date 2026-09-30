"use client";

import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import { inr, num } from "@/lib/format";

import { MapMarker, MapView } from "./MapView";
import { Badge, Button, Card, CounterfactualBadge, ErrorNote, Field, inputCls, Note, ProvenanceBadge, Table, Td, useAction, useApi } from "./ui";

/** V3-2 scenario simulator (Policy). Two channels, never blended: (B) documented assumption chain, (A) what the
 * current model does. Every output carries the COUNTERFACTUAL label (rule 22). */
export function ScenarioSimulator() {
  const types = useApi<any>("/scenarios");
  const today = new Date().toISOString().slice(0, 10);
  const ago = (d: number) => new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
  const [scenario, setScenario] = useState("rainfall_failure");
  const [districts, setDistricts] = useState<string[]>(["Kolar", "Chikkaballapur"]);
  const [deficit, setDeficit] = useState("50");
  const [start, setStart] = useState(ago(120));
  const [end, setEnd] = useState(ago(60));
  const [banStart, setBanStart] = useState(today);
  const [share, setShare] = useState("");
  const [channel, setChannel] = useState<"assumption" | "model">("assumption");
  const [week, setWeek] = useState(1);
  const [out, setOut] = useState<any>(null);
  const act = useAction();
  const all: string[] = types.data?.scenarios?.[0]?.params?.districts ?? [];

  const run = () => act.run(async () => {
    const params = scenario === "rainfall_failure"
      ? { districts, deficit: Number(deficit) / 100, start, end }
      : { start: banStart, ...(share ? { share: Number(share) / 100 } : {}) };
    setOut(await api("/scenarios/run", { method: "POST", body: { scenario, params } }));
  });

  const rows = useMemo(() => (out?.results ?? []).map((r: any) => {
    const h = r.horizons.find((x: any) => x.weeks === week);
    const s = h?.[channel];
    const pct = h && s ? (s.p50 / h.baseline.p50 - 1) * 100 : null;
    return { ...r, h, s, pct };
  }), [out, week, channel]);

  const markers = useMemo<MapMarker[]>(() => rows.filter((r: any) => r.lat != null).map((r: any) => ({
    id: r.mandi_id, lat: r.lat, lon: r.lon, kind: "status",
    color: r.pct == null ? "#898781" : r.pct > 0.5 ? "#b91c1c" : r.pct < -0.5 ? "#1d4ed8" : "#898781",
    size: 12 + Math.min(24, Math.abs(r.pct ?? 0)),
    label: r.mandi,
    popup: `${r.mandi}: ${r.pct == null ? "no model result" : `${r.pct > 0 ? "+" : ""}${num(r.pct, 1)}% p50`} (COUNTERFACTUAL ESTIMATE)`,
  })), [rows]);

  const sm = out?.summary;
  const pctOf = (m: Record<string, number | null> | null | undefined) => (m?.[String(week)] == null ? "–" : `${m[String(week)]! > 0 ? "+" : ""}${num(m[String(week)], 1)}%`);

  return (
    <Card title="Scenario simulator" action={<CounterfactualBadge />}>
      <p className="mb-3 text-sm text-ink2">
        What would a shock do to the forecast? Two separate answers: <b>(B) a documented assumption chain</b> (sourced numbers, with
        ranges) and <b>(A) what the current forecasting model does</b> when its inputs are changed. The model was trained on sample
        data, so its answer can be flat or even point the wrong way. Neither is a prediction of what will happen.
      </p>
      <div className="mb-3 grid gap-3 md:grid-cols-4">
        <Field label="Scenario">
          <select className={inputCls} value={scenario} onChange={(e) => setScenario(e.target.value)}>
            <option value="rainfall_failure">Rainfall failure in a region</option>
            <option value="export_ban">Export ban on tomato</option>
          </select>
        </Field>
        {scenario === "rainfall_failure" ? (
          <>
            <Field label="Rain lost (%)"><input className={inputCls} inputMode="numeric" value={deficit} onChange={(e) => setDeficit(e.target.value)} /></Field>
            <Field label="From"><input className={inputCls} type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label="To"><input className={inputCls} type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
            <div className="md:col-span-4">
              <span className="text-sm text-ink2">Districts</span>
              <div className="mt-1 flex flex-wrap gap-2">
                {all.map((d) => (
                  <label key={d} className="flex items-center gap-1 text-sm">
                    <input type="checkbox" checked={districts.includes(d)}
                      onChange={(e) => setDistricts((s) => e.target.checked ? [...s, d] : s.filter((x) => x !== d))} />{d}
                  </label>
                ))}
              </div>
            </div>
          </>
        ) : (
          <>
            <Field label="Ban starts"><input className={inputCls} type="date" value={banStart} onChange={(e) => setBanStart(e.target.value)} /></Field>
            <Field label={`Export share % (blank = national ${num((types.data?.scenarios?.[1]?.default_share ?? 0) * 100, 2)}%)`}>
              <input className={inputCls} inputMode="decimal" value={share} placeholder="your assumption" onChange={(e) => setShare(e.target.value)} />
            </Field>
          </>
        )}
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button onClick={run} disabled={act.busy}>{act.busy ? "Running…" : "Run scenario"}</Button>
        <ErrorNote error={act.error ?? types.error} />
      </div>

      {out && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <CounterfactualBadge text={out.label} />
            <ProvenanceBadge p={out.data_provenance} />
            <span className="text-xs text-muted">Run #{out.id} · shifts the {out.model_name} forecast issued {out.forecast_issue_date}</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-line p-3 text-sm">
              <b>(B) Assumption chain</b>: p50 {pctOf(sm.assumption_p50_shift_pct)} in the {sm.affected_mandis} affected mandis at {week} wk.
              <div className="text-xs text-muted">Multiplier {num(sm.multipliers.central, 3)} (range {num(sm.multipliers.low, 3)}–{num(sm.multipliers.high, 3)}).
                {out.scenario === "rainfall_failure" && ` Hits arrivals from ${sm.window_affects_arrivals[0]} to ${sm.window_affects_arrivals[1]} (harvest lag).`}</div>
            </div>
            <div className="rounded-lg border border-line p-3 text-sm">
              <b>(A) Current model</b>: {sm.model_available ? <>p50 {pctOf(sm.model_p50_shift_pct)} at {week} wk.</> : <>not available: {sm.model_reason}</>}
              <div className="text-xs text-muted">Trained on sample data; a sensitivity of the model, not of the market. {sm.model_notes?.join(" ")}</div>
            </div>
          </div>
          {sm.channels_disagree_weeks?.length > 0 && (
            <Note>The two channels point in opposite directions at {sm.channels_disagree_weeks.join(", ")} wk. Trust neither on its own: the
              model learned from sample data in which this link doesn&apos;t exist the way the assumption chain says.</Note>
          )}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink2">Show</span>
            {(["assumption", "model"] as const).map((c) => (
              <button key={c} onClick={() => setChannel(c)} className={`rounded-md border px-2 py-1 ${c === channel ? "border-brand bg-brand text-brand-ink" : "border-line"}`}>
                {c === "assumption" ? "(B) assumption chain" : "(A) current model"}
              </button>
            ))}
            <span className="ml-2 text-ink2">Horizon</span>
            {[1, 2, 3, 4].map((w) => (
              <button key={w} onClick={() => setWeek(w)} className={`rounded-md border px-2 py-1 ${w === week ? "border-brand bg-brand text-brand-ink" : "border-line"}`}>{w} wk</button>
            ))}
          </div>
          <MapView height="h-80" markers={markers} fitKey={out.id} />
          <p className="text-xs text-muted">Red = price up, blue = price down, grey = no change or no result; bigger = larger shift. Colour is repeated as text in the table.</p>
          <Table head={["Mandi", "Baseline p50 (p10–p90)", "Scenario p50 (p10–p90)", "Shift", "In region"]}>
            {rows.map((r: any) => (
              <tr key={r.mandi_id}>
                <Td>{r.mandi}<div className="text-xs text-muted">{r.district}</div></Td>
                <Td>{r.h ? <>{inr(r.h.baseline.p50)}<div className="text-xs text-muted">{inr(r.h.baseline.p10)}–{inr(r.h.baseline.p90)}</div></> : "–"}</Td>
                <Td>{r.s ? <>{inr(r.s.p50)}<div className="text-xs text-muted">{inr(r.s.p10)}–{inr(r.s.p90)}</div></> : "–"}</Td>
                <Td>{r.pct == null ? "–" : <Badge kind={r.pct > 0.5 ? "critical" : r.pct < -0.5 ? "good" : "neutral"}>{r.pct > 0 ? "+" : ""}{num(r.pct, 1)}%</Badge>}</Td>
                <Td>{r.in_region ? "yes" : "no"}</Td>
              </tr>
            ))}
          </Table>
          <details className="text-sm">
            <summary className="cursor-pointer text-ink2">Assumptions used, with sources</summary>
            <pre className="mt-2 overflow-x-auto rounded bg-page p-2 text-xs">{JSON.stringify(out.assumptions, null, 2)}</pre>
            <p className="text-xs text-muted">Values marked UNSOURCED are judgement calls with wide ranges. See docs/scenario-assumptions.md.</p>
          </details>
          <CounterfactualBadge text={out.label} />
        </div>
      )}
    </Card>
  );
}
