"use client";

import { useCallback, useEffect, useState } from "react";
import { ForecastRanges } from "@/components/charts";
import PricePanel from "@/components/PricePanel";
import Shell from "@/components/Shell";
import { Btn, Card, Empty, ErrorNote, Stat } from "@/components/ui";
import { api, type ForecastBlock, type Mandi, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { num, pct } from "@/lib/format";

interface Watch {
  mandi: { id: number; name: string; district: string };
  forecast: ForecastBlock | null;
  expected_tons_next_3_days: number | null;
  tons_in_transit: number | null;
  typical_daily_tons: number | null;
}

export default function BuyerPage() {
  return <Shell roles={["buyer"]} title="Bulk buyer">{(u) => <Buyer user={u} />}</Shell>;
}

function Buyer({ user }: { user: User }) {
  const { updateUser } = useAuth();
  const [data, setData] = useState<Watch[] | null>(null);
  const [mandis, setMandis] = useState<Mandi[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [watch, setWatch] = useState<Set<number>>(new Set(user.watch_mandi_ids));

  const load = useCallback(() => api<{ watching: Watch[] }>("/buyer/overview").then((r) => setData(r.watching)).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); api<Mandi[]>("/mandis").then(setMandis).catch(() => undefined); }, [load]);

  async function saveWatch() {
    const u = await api<User>("/auth/me", { method: "PATCH", body: { watch_mandi_ids: [...watch] } });
    updateUser(u);
    setEditing(false);
    load();
  }

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card
        title="Watched mandis"
        right={editing ? <Btn onClick={saveWatch}>Save</Btn> : <Btn variant="secondary" onClick={() => setEditing(true)}>Change mandis</Btn>}
      >
        {editing && (
          <div className="mb-3 grid gap-1 text-sm sm:grid-cols-3">
            {mandis.map((m) => (
              <label key={m.id} className="flex items-center gap-2">
                <input type="checkbox" checked={watch.has(m.id)} onChange={(e) => {
                  const n = new Set(watch);
                  if (e.target.checked) n.add(m.id); else n.delete(m.id);
                  setWatch(n);
                }} />
                {m.name} <span className="text-slate-400">({m.state})</span>
              </label>
            ))}
          </div>
        )}
        <p className="text-xs text-slate-500">You get a price-spike alert (in-app, email, SMS if set) when a watched mandi&apos;s 14-day spike probability crosses the alert threshold.</p>
      </Card>
      {!data ? <Empty>Loading…</Empty> : !data.length ? <Empty>Pick mandis to watch.</Empty> : data.map((w) => (
        <Card key={w.mandi.id} title={`${w.mandi.name} · ${w.mandi.district}`}>
          <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Tracked tons, next 3 days" value={num(w.expected_tons_next_3_days, 1)} sub="lower bound, tracked trucks only" />
              <Stat label="In transit now" value={num(w.tons_in_transit, 1)} />
              <Stat label="Typical daily arrivals" value={w.typical_daily_tons == null ? "–" : `${num(w.typical_daily_tons, 1)} t`} />
              <Stat label="Spike risk 14 d" value={pct(w.forecast?.spike_prob_14d)} />
            </div>
            <ForecastRanges fc={w.forecast} />
          </div>
        </Card>
      ))}
      <PricePanel title="All mandis today" />
    </div>
  );
}
