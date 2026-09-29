"use client";

import { useEffect, useState } from "react";

import { MandiForecast } from "@/components/MandiForecast";
import { Shell } from "@/components/Shell";
import { Button, Card, ErrorNote, Note, Stat, useAction, useApi } from "@/components/ui";
import { api } from "@/lib/api";
import { tons } from "@/lib/format";
import { useSession } from "@/lib/session";

export default function Buyer() {
  const { t, user, reloadUser } = useSession();
  const ov = useApi<any>("/buyer/overview", { poll: 60000 });
  const [mandis, setMandis] = useState<any[]>([]);
  const [edit, setEdit] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set(user?.watch_mandi_ids ?? []));
  const act = useAction();

  useEffect(() => { api("/mandis").then((m: any[]) => setMandis(m.filter((x) => x.lat != null))).catch(() => {}); }, []);
  useEffect(() => { setSel(new Set(user?.watch_mandi_ids ?? [])); }, [user?.watch_mandi_ids]);

  const save = () => act.run(async () => {
    await api("/auth/me", { method: "PATCH", body: { watch_mandi_ids: [...sel] } });
    await reloadUser();
    setEdit(false);
    ov.reload();
  });

  return (
    <Shell roles={["buyer"]} title="Procurement outlook">
      <Card title={t("watchlist")} action={<Button variant="secondary" onClick={() => setEdit((e) => !e)}>{edit ? "Cancel" : "Edit"}</Button>}>
        {edit ? (
          <div className="space-y-3">
            <div className="grid gap-1 sm:grid-cols-2 md:grid-cols-3">
              {mandis.map((m) => (
                <label key={m.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={sel.has(m.id)} onChange={() => setSel((s) => { const n = new Set(s); n.has(m.id) ? n.delete(m.id) : n.add(m.id); return n; })} />
                  {m.name} <span className="text-muted">{m.district}</span>
                </label>
              ))}
            </div>
            <ErrorNote error={act.error} />
            <Button disabled={act.busy} onClick={save}>Save watchlist</Button>
          </div>
        ) : (
          <p className="text-sm text-ink2">
            {ov.data?.watching?.length ? ov.data.watching.map((w: any) => w.mandi.name).join(" · ") : "You are not watching any mandi yet. Click Edit."}
          </p>
        )}
        <div className="mt-3"><Note>You get a price alert (in-app, and email / SMS if configured) when a watched mandi&apos;s 2-week spike probability crosses the alert threshold.</Note></div>
      </Card>
      <ErrorNote error={ov.error} />
      {ov.data?.watching?.map((w: any) => (
        <Card key={w.mandi.id} title={`${w.mandi.name} · ${w.mandi.district}`}>
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Stat label={t("expected3d")} value={tons(w.expected_tons_next_3_days)} sub="tracked vehicles only" />
            <Stat label={t("inTransit")} value={tons(w.tons_in_transit)} />
            <Stat label={t("typicalDay")} value={w.typical_daily_tons != null ? tons(w.typical_daily_tons) : "–"} />
          </div>
          <MandiForecast mandiId={w.mandi.id} />
        </Card>
      ))}
    </Shell>
  );
}
