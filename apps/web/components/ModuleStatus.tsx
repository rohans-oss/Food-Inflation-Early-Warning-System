"use client";

import { Badge, Card, ProvenanceBadge, Table, Td, useApi } from "./ui";

/** V3-3 rule 23: what is real today, module by module (Policy + Admin). */
export function ModuleStatus() {
  const st = useApi<any[]>("/module-status");
  return (
    <Card title="What is real today, by module" action={<span className="text-xs text-muted">rule 23: never one system-wide switch</span>}>
      <Table head={["Module", "Data status", "Evidence", "Details"]} empty={st.loading ? "Loading…" : "No status."}>
        {st.data?.map((m) => (
          <tr key={m.module}>
            <Td className="font-medium">{m.module}</Td>
            <Td>
              {m.status === "not_yet_evaluable" ? <Badge kind="warn">not yet evaluable</Badge> : <ProvenanceBadge p={m.status} />}
              {m.counterfactual && <div className="mt-1"><Badge kind="warn">counterfactual</Badge></div>}
            </Td>
            <Td className="max-w-lg text-sm">{m.evidence}
              {m.per_mandi && <div className="text-xs text-muted">{Object.entries(m.per_mandi).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>}
            </Td>
            <Td><span className="font-mono text-xs text-muted">{m.doc}</span></Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
