"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Btn, Card, Empty } from "@/components/ui";
import { api, type Role } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { KN_REVIEWED } from "@/lib/i18n";

interface Alert { id: number; kind: string; severity: string; title: string; body: string; lang: string; created_at: string; read_at: string | null; channels: Record<string, string> | null }

const ALL: Role[] = ["farmer", "fpo", "driver", "fleet_owner", "trader", "buyer", "policy", "lender", "admin"];
const SEV = { critical: "border-l-red-600", warning: "border-l-amber-500", info: "border-l-sky-500" } as Record<string, string>;

export default function AlertsPage() {
  return <Shell roles={ALL} title="Alerts">{() => <Alerts />}</Shell>;
}

function Alerts() {
  const [items, setItems] = useState<Alert[] | null>(null);
  const load = useCallback(() => api<Alert[]>("/alerts").then(setItems).catch(() => setItems([])), []);
  useEffect(() => { load(); }, [load]);
  const read = async (id: number) => { await api(`/alerts/${id}/read`, { method: "POST" }); load(); };

  return (
    <Card>
      {!items ? <Empty>Loading…</Empty> : !items.length ? <Empty>No alerts yet.</Empty> : (
        <ul className="space-y-2">
          {items.map((a) => (
            <li key={a.id} className={`rounded border border-l-4 border-slate-200 p-3 ${SEV[a.severity] || ""} ${a.read_at ? "opacity-60" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{a.title}</div>
                  <div className="text-sm text-slate-700">{a.body}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {dateTime(a.created_at)} · {a.kind}
                    {a.channels && Object.keys(a.channels).length > 0 && <> · {Object.entries(a.channels).map(([c, s]) => `${c}: ${s}`).join(", ")}</>}
                    {a.lang === "kn" && !KN_REVIEWED && <> · Kannada text not yet reviewed</>}
                  </div>
                </div>
                {!a.read_at && <Btn variant="secondary" onClick={() => read(a.id)}>Mark read</Btn>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
