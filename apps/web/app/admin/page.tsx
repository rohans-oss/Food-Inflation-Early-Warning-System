"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Btn, Card, Empty, ErrorNote, Field, inputCls, Stat, Status, SyntheticBadge, Table } from "@/components/ui";
import { api, type User } from "@/lib/api";
import { dateTime, day, num } from "@/lib/format";
import { usePoll } from "@/lib/live";

interface Fresh { source: string; last_success_at: string | null; last_success_rows: number | null; last_run_status: string | null; age_hours: number | null; status: string; budget_hours: number }
interface Run { id: number; source: string; status: string; started_at: string; finished_at: string | null; rows: number | null; error: string | null }
interface DQ { mandi_id: number; mandi: string; coords_verified: boolean; days_with_price: number; missing_day_pct: number; outliers: number; last_price_date: string | null }
interface HMetric { pinball: number; mape_pct: number; coverage_p10_p90_pct: number; n: number }
interface Perf {
  available: boolean; hint?: string; model_version?: string; trained_on_synthetic?: boolean; trained_at?: string; data_range?: [string, string];
  mandis?: number; rows?: number; spike_definition?: string; vs_naive_pinball_pct?: Record<string, number>;
  metrics?: Record<string, { by_horizon: Record<string, HMetric>; spike: { events: number; recall: number | null; precision: number | null; brier: number | null } }>;
  folds?: unknown[];
}

const TABS = ["Data freshness", "Failed jobs", "Data quality", "Model performance", "Users", "Simulator"] as const;

export default function AdminPage() {
  return <Shell roles={["admin"]} title="Admin / data ops">{(u) => <Admin me={u} />}</Shell>;
}

function Admin({ me }: { me: User }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Data freshness");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-green-700 font-medium text-green-800" : "border-transparent text-slate-600"}`}>{t}</button>
        ))}
      </div>
      {tab === "Data freshness" && <Freshness />}
      {tab === "Failed jobs" && <Runs />}
      {tab === "Data quality" && <Quality />}
      {tab === "Model performance" && <Model />}
      {tab === "Users" && <Users me={me} />}
      {tab === "Simulator" && <Simulator />}
    </div>
  );
}

function Freshness() {
  const [d, setD] = useState<{ sources: Fresh[]; latest_price_date: string | null } | null>(null);
  usePoll(() => api<typeof d>("/admin/freshness").then(setD).catch(() => undefined), 60000);
  if (!d) return <Empty>Loading…</Empty>;
  return (
    <Card title="Last successful pull per source">
      <div className="mb-3 grid gap-3 sm:grid-cols-4">
        {d.sources.map((s) => (
          <Stat key={s.source} label={s.source} value={<Status s={s.status} />} sub={s.age_hours == null ? "never succeeded" : `${num(s.age_hours, 1)} h ago (budget ${s.budget_hours} h)`} />
        ))}
      </div>
      <Table head={["Source", "Last success", "Rows", "Last run", "Status"]}>
        {d.sources.map((s) => (
          <tr key={s.source}>
            <td className="px-2 py-1.5 font-medium">{s.source}</td>
            <td className="px-2 py-1.5">{dateTime(s.last_success_at)}</td>
            <td className="px-2 py-1.5">{s.last_success_rows ?? "–"}</td>
            <td className="px-2 py-1.5"><Status s={s.last_run_status} /></td>
            <td className="px-2 py-1.5"><Status s={s.status} /></td>
          </tr>
        ))}
      </Table>
      <p className="mt-2 text-xs text-slate-500">Latest price date in the database: {day(d.latest_price_date)}. Agmarknet and NASA POWER are daily, Open-Meteo hourly, forecasts nightly.</p>
    </Card>
  );
}

function Runs() {
  const [status, setStatus] = useState("failed");
  const [runs, setRuns] = useState<Run[] | null>(null);
  useEffect(() => { setRuns(null); api<Run[]>(`/admin/runs?limit=100${status ? `&status=${status}` : ""}`).then(setRuns).catch(() => setRuns([])); }, [status]);
  return (
    <Card title="Job log" right={
      <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="failed">Failed only</option>
        <option value="">All runs</option>
      </select>
    }>
      {!runs ? <Empty>Loading…</Empty> : !runs.length ? <Empty>No {status || ""} runs.</Empty> : (
        <Table head={["#", "Source", "Status", "Started", "Finished", "Rows", "Error"]}>
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="px-2 py-1.5">{r.id}</td>
              <td className="px-2 py-1.5">{r.source}</td>
              <td className="px-2 py-1.5"><Status s={r.status} /></td>
              <td className="px-2 py-1.5">{dateTime(r.started_at)}</td>
              <td className="px-2 py-1.5">{dateTime(r.finished_at)}</td>
              <td className="px-2 py-1.5">{r.rows ?? "–"}</td>
              <td className="max-w-md px-2 py-1.5 text-xs text-red-700"><code className="break-all">{r.error}</code></td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function Quality() {
  const [rows, setRows] = useState<DQ[] | null>(null);
  const [edit, setEdit] = useState<{ id: number; lat: string; lon: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api<DQ[]>("/admin/data-quality").then(setRows).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!rows) return <Empty>Loading…</Empty>;
  return (
    <Card title="Price coverage, last 90 days">
      <ErrorNote error={error} />
      <Table head={["Mandi", "Days with price", "Missing days", "Outliers flagged", "Last price", "Location"]}>
        {rows.map((r) => (
          <tr key={r.mandi_id}>
            <td className="px-2 py-1.5">{r.mandi}</td>
            <td className="px-2 py-1.5">{r.days_with_price}</td>
            <td className={`px-2 py-1.5 ${r.missing_day_pct > 30 ? "font-semibold text-red-700" : ""}`}>{r.missing_day_pct}%</td>
            <td className="px-2 py-1.5">{r.outliers}</td>
            <td className="px-2 py-1.5">{day(r.last_price_date)}</td>
            <td className="px-2 py-1.5">
              {edit?.id === r.mandi_id ? (
                <form className="flex gap-1" onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await api(`/admin/mandis/${r.mandi_id}`, { method: "PATCH", body: { lat: Number(edit.lat), lon: Number(edit.lon), coords_verified: true } });
                    setEdit(null);
                    load();
                  } catch (err) { setError((err as Error).message); }
                }}>
                  <input className={`${inputCls} w-24`} placeholder="lat" value={edit.lat} onChange={(e) => setEdit({ ...edit, lat: e.target.value })} />
                  <input className={`${inputCls} w-24`} placeholder="lon" value={edit.lon} onChange={(e) => setEdit({ ...edit, lon: e.target.value })} />
                  <Btn type="submit">Verify</Btn>
                </form>
              ) : (
                <>
                  {r.coords_verified ? <Status s="success" /> : <span className="text-xs text-amber-700">unverified</span>}{" "}
                  <button className="text-xs text-green-800 underline" onClick={() => setEdit({ id: r.mandi_id, lat: "", lon: "" })}>set</button>
                </>
              )}
            </td>
          </tr>
        ))}
      </Table>
      <p className="mt-2 text-xs text-slate-500">Outliers are flagged by the cleaning layer (min &gt; max, modal outside range, non-positive, &gt;5× jump vs 30-day median) and kept out of models and dashboards. Unknown markets arrive without coordinates and stay off maps until verified here.</p>
    </Card>
  );
}

function Model() {
  const [p, setP] = useState<Perf | null>(null);
  useEffect(() => { api<Perf>("/admin/model-performance").then(setP).catch(() => undefined); }, []);
  if (!p) return <Empty>Loading…</Empty>;
  if (!p.available) return <Card title="Model performance"><Empty>{p.hint}</Empty></Card>;
  const models = Object.keys(p.metrics || {});
  const hs = Object.keys(p.metrics?.[models[0]]?.by_horizon || {});
  return (
    <div className="space-y-4">
      <Card title={<>Walk-forward backtest · {p.model_version} <SyntheticBadge on={p.trained_on_synthetic} /></>}>
        <p className="text-sm text-slate-600">
          Data {p.data_range?.join(" → ")} · {p.mandis} mandis · {p.rows?.toLocaleString()} rows · {p.folds?.length} folds · trained {dateTime(p.trained_at)}.
          Spike = {p.spike_definition}.
        </p>
        {p.trained_on_synthetic && (
          <p className="mt-1 text-xs font-medium text-amber-800">These numbers come from synthetic history and only show the pipeline works. They say nothing about real-world accuracy.</p>
        )}
      </Card>
      <Card title="Pinball loss (Rs/quintal, lower is better) · MAPE of p50 · p10–p90 coverage (target 80%)">
        <Table head={["Model", ...hs.map((h) => `+${h.slice(1)} wk`)]}>
          {models.map((m) => (
            <tr key={m}>
              <td className="px-2 py-1.5 font-medium">{m}</td>
              {hs.map((h) => {
                const x = p.metrics![m].by_horizon[h];
                return <td key={h} className="px-2 py-1.5 text-xs">{num(x.pinball, 0)} · {num(x.mape_pct, 1)}% · {num(x.coverage_p10_p90_pct, 0)}%</td>;
              })}
            </tr>
          ))}
        </Table>
        {p.vs_naive_pinball_pct && (
          <p className="mt-2 text-sm">LightGBM vs naive (pinball improvement): {Object.entries(p.vs_naive_pinball_pct).map(([h, v]) => `+${h.slice(1)}wk ${v > 0 ? "+" : ""}${v}%`).join(" · ")}</p>
        )}
      </Card>
      <Card title="Spike detection (14 days ahead)">
        <Table head={["Model", "Events", "Recall", "Precision", "Brier"]}>
          {models.map((m) => {
            const s = p.metrics![m].spike;
            return (
              <tr key={m}>
                <td className="px-2 py-1.5 font-medium">{m}</td>
                <td className="px-2 py-1.5">{s.events}</td>
                <td className="px-2 py-1.5">{s.recall ?? "–"}</td>
                <td className="px-2 py-1.5">{s.precision ?? "–"}</td>
                <td className="px-2 py-1.5">{s.brier ?? "–"}</td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </div>
  );
}

function Users({ me }: { me: User }) {
  const [users, setUsers] = useState<(User & { is_active: boolean })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api<(User & { is_active: boolean })[]>("/admin/users").then(setUsers).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const patch = async (id: number, body: Record<string, unknown>) => {
    setError(null);
    try { await api(`/admin/users/${id}`, { method: "PATCH", body }); load(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Card title="Users">
      <ErrorNote error={error} />
      {!users ? <Empty>Loading…</Empty> : (
        <Table head={["#", "Name", "Email", "Role", "Organization", "Language", "Status", ""]}>
          {users.map((u) => (
            <tr key={u.id}>
              <td className="px-2 py-1.5">{u.id}</td>
              <td className="px-2 py-1.5">{u.full_name}</td>
              <td className="px-2 py-1.5 text-slate-600">{u.email}</td>
              <td className="px-2 py-1.5">{u.role_label}</td>
              <td className="px-2 py-1.5">{u.org_name || "–"}</td>
              <td className="px-2 py-1.5">{u.preferred_lang}</td>
              <td className="px-2 py-1.5">{u.is_active ? <Status s="success" /> : <Status s="declined" />}</td>
              <td className="px-2 py-1.5">
                {u.id !== me.id && (
                  <Btn variant="secondary" onClick={() => patch(u.id, { is_active: !u.is_active })}>{u.is_active ? "Disable" : "Enable"}</Btn>
                )}
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function Simulator() {
  const [st, setSt] = useState<{ running: boolean; trips: number; speedup: number; started_at: string | null } | null>(null);
  const [f, setF] = useState({ trips: "8", speedup: "10" });
  const [error, setError] = useState<string | null>(null);
  const load = () => api<typeof st>("/admin/simulator").then(setSt).catch((e) => setError(e.message));
  usePoll(load, 10000);
  const run = async (path: string, body?: unknown) => {
    setError(null);
    try { await api(path, { method: "POST", body }); load(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Card title="Trip simulator (synthetic)">
      <p className="text-sm text-slate-600">
        Generates synthetic trucks driving real routes (OSRM when configured) to mandis. Every simulated vehicle, trip and lot is stored with
        <code> is_simulated = true</code> and labelled <b>Simulated</b> on every screen. Use it for demos and load tests, never as evidence.
      </p>
      <ErrorNote error={error} />
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Field label="Trips"><input className={`${inputCls} w-24`} type="number" min={1} max={100} value={f.trips} onChange={(e) => setF({ ...f, trips: e.target.value })} /></Field>
        <Field label="Speed-up ×"><input className={`${inputCls} w-24`} type="number" min={0.5} max={120} value={f.speedup} onChange={(e) => setF({ ...f, speedup: e.target.value })} /></Field>
        <Btn onClick={() => run("/admin/simulator/start", { trips: Number(f.trips), speedup: Number(f.speedup) })} disabled={st?.running}>Start</Btn>
        <Btn variant="danger" onClick={() => run("/admin/simulator/stop")} disabled={!st?.running}>Stop and clean up</Btn>
      </div>
      {st && <p className="mt-2 text-sm">Status: {st.running ? <Status s="running" /> : <Status s="never" />} · {st.trips} trips · ×{st.speedup} · since {dateTime(st.started_at)}</p>}
    </Card>
  );
}
