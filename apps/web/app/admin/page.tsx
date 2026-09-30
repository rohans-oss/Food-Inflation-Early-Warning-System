"use client";

import { useState } from "react";

import { ConnectedMandis } from "@/components/ConnectedMandis";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, Field, inputCls, Note, ProvenanceBadge, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, dateTime, day, num } from "@/lib/format";
import { useSession } from "@/lib/session";

const SOURCE_LABEL: Record<string, string> = {
  agmarknet: "Agmarknet prices (data.gov.in)",
  open_meteo: "Open-Meteo weather",
  nasa_power: "NASA POWER weather",
  forecast: "Daily forecast job",
  graph_build: "Mandi graph (weekly)",
};
const ROLES = ["farmer", "fpo", "driver", "fleet_owner", "trader", "buyer", "policy", "lender", "admin"];

export default function Admin() {
  const { t } = useSession();
  const fresh = useApi<any>("/admin/freshness", { poll: 60000 });
  const failed = useApi<any[]>("/admin/runs", { query: { status: "failed", limit: 30 }, poll: 60000 });
  const dq = useApi<any[]>("/admin/data-quality");
  const perf = useApi<any>("/admin/model-performance");
  const users = useApi<any[]>("/admin/users");
  const sim = useApi<any>("/admin/simulator", { poll: 10000 });
  const ready = useApi<any>("/admin/data-readiness", { poll: 300000 });
  const runs = useApi<any[]>("/admin/eval-runs", { query: { limit: 10 } });
  const mandiList = useApi<any[]>("/mandis");
  const v2 = useApi<any[]>("/admin/v2-results");
  const [simCfg, setSimCfg] = useState({ trips: "8", speedup: "10" });
  const act = useAction();

  const patchUser = (id: number, body: object) => act.run(async () => { await api(`/admin/users/${id}`, { method: "PATCH", body }); users.reload(); });
  const simStart = () => act.run(async () => {
    await api("/admin/simulator/start", { method: "POST", body: { trips: Number(simCfg.trips), speedup: Number(simCfg.speedup) } });
    sim.reload();
  });
  const simStop = () => act.run(async () => { await api("/admin/simulator/stop", { method: "POST" }); sim.reload(); });

  const p = perf.data;
  return (
    <Shell roles={["admin"]} title="Data operations" wide>
      <ErrorNote error={act.error} />
      <Card title={t("freshness")} action={<span className="text-xs text-muted">latest price date: {day(fresh.data?.latest_price_date)}</span>}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {fresh.data?.sources?.map((s: any) => (
            <div key={s.source} className="rounded-xl border border-line p-3">
              <div className="flex items-center justify-between gap-2 text-sm"><b>{SOURCE_LABEL[s.source] ?? s.source}</b><StatusBadge s={s.status} /></div>
              <div className="mt-2 text-sm">Last success {ago(s.last_success_at)}</div>
              <div className="text-xs text-muted">{s.last_success_rows ?? 0} rows · expected every {s.budget_hours} h · last run {s.last_run_status ?? "–"}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t("failedJobs")}>
          <Table head={["When", "Source", "Error"]} empty="No failed runs.">
            {failed.data?.map((r) => (
              <tr key={r.id}><Td>{dateTime(r.started_at)}</Td><Td>{r.source}</Td><Td className="max-w-md break-words font-mono text-xs">{r.error}</Td></tr>
            ))}
          </Table>
        </Card>
        <Card title={t("simulator")} action={sim.data?.running ? <Badge kind="sim">Running · {sim.data.trips} trips</Badge> : <Badge>Stopped</Badge>}>
          <p className="mb-3 text-sm text-ink2">Creates synthetic trucks on real routes, fed through the same GPS pipeline as real phones. Every one is flagged <b>Simulated</b> everywhere it appears.</p>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Trips"><input className={`${inputCls} w-24`} type="number" min="1" max="100" value={simCfg.trips} onChange={(e) => setSimCfg({ ...simCfg, trips: e.target.value })} /></Field>
            <Field label="Speed-up ×"><input className={`${inputCls} w-24`} type="number" min="0.5" max="120" value={simCfg.speedup} onChange={(e) => setSimCfg({ ...simCfg, speedup: e.target.value })} /></Field>
            {sim.data?.running ? <Button variant="danger" onClick={simStop} disabled={act.busy}>Stop and cancel trips</Button>
              : <Button onClick={simStart} disabled={act.busy}>Start</Button>}
          </div>
        </Card>
      </div>

      <Card title={t("modelPerformance")} action={p?.available && <ProvenanceBadge p={p.data_provenance} />}>
        {!p?.available ? <p className="text-sm text-muted">{p?.hint ?? t("loading")}</p> : (
          <div className="space-y-3">
            <p className="text-sm text-ink2">Model {p.model_version} · data {p.data_range?.join(" → ")} · {p.mandis} mandis · {p.folds?.length} walk-forward folds · spike = {p.spike_definition}</p>
            <Table head={["Horizon", ...Object.keys(p.metrics).map((m) => `${m} pinball`), "LightGBM vs naive", "LightGBM p10–p90 coverage", "LightGBM MAPE"]}>
              {["h1", "h2", "h3", "h4"].map((h) => (
                <tr key={h}>
                  <Td>{h.slice(1)} wk</Td>
                  {Object.keys(p.metrics).map((m) => <Td key={m}>{num(p.metrics[m].by_horizon[h].pinball, 0)}</Td>)}
                  <Td><b>{p.vs_naive_pinball_pct?.[h] > 0 ? `${num(p.vs_naive_pinball_pct[h], 1)}% better` : `${num(-p.vs_naive_pinball_pct?.[h], 1)}% worse`}</b></Td>
                  <Td>{num(p.metrics.lightgbm_quantile.by_horizon[h].coverage_p10_p90_pct, 0)}% <span className="text-muted">(target 80%)</span></Td>
                  <Td>{num(p.metrics.lightgbm_quantile.by_horizon[h].mape_pct, 1)}%</Td>
                </tr>
              ))}
            </Table>
            <Table head={["Model", "Spike events", "Recall", "Precision", "False-alarm rate", "Brier"]}>
              {Object.entries(p.metrics).map(([m, v]: [string, any]) => (
                <tr key={m}><Td>{m}</Td><Td>{v.spike.events}</Td><Td>{v.spike.recall ?? "–"}</Td><Td>{v.spike.precision ?? "–"}</Td>
                  <Td>{v.spike.false_alarm_rate ?? "–"}</Td><Td>{v.spike.brier ?? "–"}</Td></tr>
              ))}
            </Table>
            {p.data_provenance === "synthetic" && <Note>SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT. These numbers come from one
              synthetic draw and prove only that the pipeline runs. &ldquo;Better&rdquo; here is not evidence: across 8 synthetic draws
              LightGBM has no reliable edge over naive (docs/backtest-synthetic.md). Re-train on real Agmarknet history before quoting any result.</Note>}
            {p.data_provenance === "real_partial" && <Note>REAL — LIMITED HISTORY: some mandis are below the readiness threshold, so these numbers are partial.</Note>}
          </div>
        )}
      </Card>

      <Card title={t("readiness")} action={ready.data && <span className="text-xs text-muted">thresholds from {ready.data.config_file} · real rows only</span>}>
        {ready.data && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {(["prices", "weather", "weather_forecasts", "arrivals", "transit"] as const).map((g) => {
                const s = ready.data.summary[g];
                const th = ready.data.thresholds[g];
                return (
                  <div key={g} className="rounded-xl border border-line p-3 text-sm">
                    <div className="flex items-center justify-between"><b className="capitalize">{g.replace("_", " ")}</b>
                      <span className="tnum">{s.ready}/{s.total} ready</span></div>
                    <div className="mt-1 text-xs text-muted">
                      {g === "transit" ? `needs ${th.min_real_trips} real trips over ${th.min_days_covered} days`
                        : `needs ${th.min_real_days} days, ≤ ${th.max_missing_pct}% missing`}
                    </div>
                    <div className="mt-2 text-xs">{s.no_data === s.total ? "No real data yet"
                      : s.latest_projected_ready_date ? `All collecting mandis ready by ${day(s.latest_projected_ready_date)}` : ""}</div>
                  </div>
                );
              })}
            </div>
            <Table head={["Mandi", "Prices", "Weather", "Forecast archive", "Arrivals", "Real trips"]}>
              {ready.data.mandis.map((m: any) => (
                <tr key={m.mandi_id}>
                  <Td>{m.mandi}<div className="text-xs text-muted">{m.district}</div></Td>
                  {(["prices", "weather", "weather_forecasts", "arrivals"] as const).map((g) => (
                    <Td key={g}>
                      <StatusBadge s={m[g].status} />
                      <div className="text-xs text-muted tnum">
                        {m[g].history_days ? `${m[g].history_days} days · ${num(m[g].missing_pct, 0)}% missing` : ""}
                        {m[g].projected_ready_date ? ` · ready ~${day(m[g].projected_ready_date)}` : ""}
                      </div>
                    </Td>
                  ))}
                  <Td><StatusBadge s={m.transit.status} /><div className="text-xs text-muted tnum">{m.transit.real_trips} trips</div></Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>

      <Card title={t("evalRuns")}>
        <Table head={["When", "Purpose", "Feature set", "Models", "Data", "Folds", "Provenance"]} empty="No evaluation runs recorded yet.">
          {runs.data?.map((r) => (
            <tr key={r.run_id}>
              <Td>{dateTime(r.started_at)}</Td>
              <Td>{r.purpose || "–"}{r.purpose?.includes("planted") && <div className="mt-1"><Badge kind="sim">PLANTED SIGNAL · positive control</Badge></div>}</Td><Td className="font-mono text-xs">{r.feature_set}</Td>
              <Td>{r.models.join(", ")}</Td><Td>{day(r.data_start)} → {day(r.data_end)}<div className="text-xs text-muted">{r.n_mandis} mandis</div></Td>
              <Td>{r.folds}</Td><Td><ProvenanceBadge p={r.data_provenance} compact /></Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title={t("v2Results")} action={<span className="text-xs text-muted">details and numbers in each linked doc</span>}>
        <Table head={["Phase", "Study", "Result", "Outcome", "Data"]} empty="No V2 results listed.">
          {v2.data?.map((r) => (
            <tr key={`${r.phase}-${r.title}`}>
              <Td>{r.phase}</Td>
              <Td>{r.title}<div className="font-mono text-xs text-muted">{r.doc}</div></Td>
              <Td className="max-w-md">{r.headline}</Td>
              <Td><Badge kind={r.outcome === "negative" ? "critical" : r.outcome === "not enough real data" ? "warn" : "neutral"}>{r.outcome}</Badge></Td>
              <Td><ProvenanceBadge p={r.data_provenance} compact /></Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title={t("connectedMandis")}>
        {mandiList.data && <ConnectedMandis mandis={mandiList.data.map((m) => ({ id: m.id, name: m.name }))} />}
      </Card>

      <Card title={t("dataQuality")}>
        <Table head={["Mandi", "Days with a price", "Missing days", "Outliers flagged", "Last price", "Location"]}>
          {dq.data?.map((r) => (
            <tr key={r.mandi_id}>
              <Td>{r.mandi}</Td><Td>{r.days_with_price}/90</Td>
              <Td>{r.missing_day_pct > 50 ? <Badge kind="critical">{num(r.missing_day_pct, 0)}%</Badge> : `${num(r.missing_day_pct, 0)}%`}</Td>
              <Td>{r.outliers}</Td><Td>{day(r.last_price_date)}</Td>
              <Td>{r.coords_verified ? "verified" : <span className="text-muted">unverified</span>}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title={t("users")}>
        <Table head={["Name", "Email", "Role", "Organization", "Active"]}>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <Td>{u.full_name}</Td><Td>{u.email}</Td>
              <Td><select aria-label={`Role of ${u.full_name}`} className="rounded border border-line bg-surface px-1 py-0.5 text-sm" value={u.role}
                onChange={(e) => patchUser(u.id, { role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Td>
              <Td>{u.org_name ?? "–"}</Td>
              <Td><input type="checkbox" aria-label={`Active: ${u.full_name}`} checked={u.is_active ?? true} onChange={(e) => patchUser(u.id, { is_active: e.target.checked })} /></Td>
            </tr>
          ))}
        </Table>
      </Card>
    </Shell>
  );
}
