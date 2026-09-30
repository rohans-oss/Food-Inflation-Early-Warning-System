"use client";

import { useState } from "react";

import { ConnectedMandis } from "@/components/ConnectedMandis";
import { Shell } from "@/components/Shell";
import { Badge, Button, CalibrationBadge, Card, SimBadge, ErrorNote, Field, inputCls, Note, ProvenanceBadge, StatusBadge, Table, Td, useAction, useApi } from "@/components/ui";
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

      <CompareRecommenders />

      <Card title={t("v2Results")} action={<span className="text-xs text-muted">details and numbers in each linked doc</span>}>
        <Table head={["Phase", "Study", "Result", "Outcome", "Data"]} empty="No V2 results listed.">
          {v2.data?.map((r) => (
            <tr key={`${r.phase}-${r.title}`}>
              <Td>{r.phase}</Td>
              <Td>{r.title}<div className="font-mono text-xs text-muted">{r.doc}</div></Td>
              <Td className="max-w-md">{r.headline}{r.calibration && <div className="mt-1"><CalibrationBadge c={r.calibration} /></div>}</Td>
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
        <Table head={["Name", "Email", "Role", "Organization", "Active", "Sessions"]}>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <Td>{u.full_name}</Td><Td>{u.email}</Td>
              <Td><select aria-label={`Role of ${u.full_name}`} className="rounded border border-line bg-surface px-1 py-0.5 text-sm" value={u.role}
                onChange={(e) => patchUser(u.id, { role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Td>
              <Td>{u.org_name ?? "–"}</Td>
              <Td><input type="checkbox" aria-label={`Active: ${u.full_name}`} checked={u.is_active ?? true} onChange={(e) => patchUser(u.id, { is_active: e.target.checked })} /></Td>
              <Td><RevokeSessions user={u} onDone={users.reload} /></Td>
            </tr>
          ))}
        </Table>
      </Card>
    </Shell>
  );
}

/** Pre-V3 B-3: sign a user out on every device now (audited: who, when, optional reason). Not the same as disabling. */
function RevokeSessions({ user, onDone }: { user: any; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const act = useAction();
  const n = user.active_sessions ?? 0;
  if (!open) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <span className="text-ink2">{n} active</span>
        <button className="text-sm text-critical underline disabled:no-underline disabled:opacity-50" disabled={n === 0}
          onClick={() => setOpen(true)}>Revoke all</button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="w-48"><input className={inputCls} placeholder="Reason (optional)" value={reason} maxLength={200}
        aria-label={`Reason for revoking ${user.full_name}'s sessions`} onChange={(e) => setReason(e.target.value)} /></div>
      <Button onClick={() => act.run(async () => {
        await api(`/admin/users/${user.id}/revoke-sessions`, { method: "POST", body: { reason: reason || null } });
        setOpen(false); setReason(""); onDone();
      })} disabled={act.busy}>Revoke {n}</Button>
      <button className="text-sm text-ink2" onClick={() => setOpen(false)}>Cancel</button>
      <ErrorNote error={act.error} />
    </div>
  );
}

/** V3-0: the OLD V1 rule vs the OR-Tools optimizer on the same simulated batch of lots (rule 21). */
function CompareRecommenders() {
  const rec = useApi<any>("/admin/recommenders");
  const [density, setDensity] = useState("medium");
  const [seed, setSeed] = useState("1");
  const [out, setOut] = useState<any>(null);
  const act = useAction();
  const run = () => act.run(async () => setOut(await api("/admin/recommenders/compare", { method: "POST", body: { density, seed: Number(seed) || 0 } })));
  const LABEL: Record<string, string> = { rule: "V1 rule (old)", optimizer: "Optimizer", optimizer_p10: "Optimizer, risk-averse (p10)" };
  const inr = (v: number) => "₹" + num(v, 0);
  return (
    <Card title="Recommenders: V1 rule vs optimizer" action={<span className="text-xs text-muted">study: docs/optimizer-results.md</span>}>
      <p className="mb-3 text-sm text-ink2">
        Users get <b>{rec.data?.default === "optimizer" ? "the optimizer" : "the V1 rule"}</b> (<code>[recommender] default</code> in
        config/recommender.toml). Compare both on one simulated batch of lots and trucks, using today&apos;s forecasts.
      </p>
      <div className="mb-3 flex flex-wrap items-end gap-2">
        <Field label="Batch">
          <select className={inputCls} value={density} onChange={(e) => setDensity(e.target.value)}>
            <option value="sparse">Sparse: 6 lots, 8 trucks</option>
            <option value="medium">Medium: 20 lots, 18 trucks</option>
            <option value="dense_tight">Dense: 60 lots, trucks for 70% of the tonnes</option>
          </select>
        </Field>
        <Field label="Seed"><div className="w-24"><input className={inputCls} value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" /></div></Field>
        <Button onClick={run} disabled={act.busy}>{act.busy ? "Solving…" : "Compare"}</Button>
      </div>
      <ErrorNote error={act.error} />
      {out && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
            <SimBadge on label="Simulated lots and trucks" />
            <ProvenanceBadge p={out.data_provenance} compact />
            <Badge>{out.distances}</Badge>
            <span className="text-ink2">{out.n_lots} lots, {out.tons_offered} t · {out.n_vehicles} trucks, {out.vehicle_capacity} t</span>
          </div>
          <Table head={["Method", "Net value", "Transport", "Spoilage loss", "Lots shipped", "Violations", "Solve"]}>
            {out.results.map((r: any) => (
              <tr key={r.method}>
                <Td>{LABEL[r.method] ?? r.method}{r.status !== "ok" && <div className="text-xs text-muted">{r.status}</div>}</Td>
                <Td>{inr(r.net_value)}</Td><Td>{inr(r.transport_cost)}</Td><Td>{inr(r.spoilage_loss)}</Td>
                <Td>{r.lots_shipped} / {r.lots_shipped + r.lots_unserved}</Td>
                <Td>{r.violations_total ? <Badge kind="critical">{r.violations_total}</Badge> : "0"}
                  {r.violations_mandi_over_tons > 0 && <div className="text-xs text-muted">{num(r.violations_mandi_over_tons, 1)} t over mandi room</div>}
                  {r.violations_spoilage_lots > 0 && <div className="text-xs text-muted">{r.violations_spoilage_lots} lots over spoilage cap</div>}</Td>
                <Td>{num(r.solve_seconds, 2)} s</Td>
              </tr>
            ))}
          </Table>
          {out.mandis_with_known_room < out.mandis && (
            <Note>{out.mandis - out.mandis_with_known_room} of {out.mandis} mandis have no arrivals history, so no mandi-room limit applies to them and
              overloading them can&apos;t show up as a violation.</Note>
          )}
          <p className="mt-2 text-xs text-muted">A planning view, scored at the forecast p50; the study (docs/optimizer-results.md) scores decisions
            at realised prices. Mandi overload is counted, not priced, so the rule can look richer by flooding a mandi.</p>
        </>
      )}
    </Card>
  );
}
