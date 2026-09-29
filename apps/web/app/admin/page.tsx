"use client";

import { useState } from "react";

import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, Field, inputCls, Note, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, dateTime, day, num } from "@/lib/format";
import { useSession } from "@/lib/session";

const SOURCE_LABEL: Record<string, string> = {
  agmarknet: "Agmarknet prices (data.gov.in)",
  open_meteo: "Open-Meteo weather",
  nasa_power: "NASA POWER weather",
  forecast: "Daily forecast job",
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

      <Card title={t("modelPerformance")} action={p?.trained_on_synthetic && <Badge kind="sim">Trained on synthetic data</Badge>}>
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
            <Table head={["Model", "Spike events", "Recall", "Precision", "Brier"]}>
              {Object.entries(p.metrics).map(([m, v]: [string, any]) => (
                <tr key={m}><Td>{m}</Td><Td>{v.spike.events}</Td><Td>{v.spike.recall ?? "–"}</Td><Td>{v.spike.precision ?? "–"}</Td><Td>{v.spike.brier ?? "–"}</Td></tr>
              ))}
            </Table>
            {p.trained_on_synthetic && <Note>These numbers come from synthetic data and prove only that the pipeline runs. Re-train on real Agmarknet history before quoting any result.</Note>}
          </div>
        )}
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
