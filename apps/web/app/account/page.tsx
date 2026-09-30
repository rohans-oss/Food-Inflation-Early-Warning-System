"use client";

import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorNote, Table, Td, useAction, useApi } from "@/components/ui";
import { api, Role, ROLE_HOME } from "@/lib/api";
import { dateTime } from "@/lib/format";

/** V3-3 (backlog 19): where am I signed in, and sign out one device. Every role. */
export default function Account() {
  const list = useApi<any[]>("/auth/sessions");
  const act = useAction();
  const out = (id: string) => act.run(async () => { await api(`/auth/sessions/${id}/revoke`, { method: "POST" }); list.reload(); });
  return (
    <Shell roles={Object.keys(ROLE_HOME) as Role[]} title="Account">
      <Card title="Where you're signed in">
        <ErrorNote error={act.error ?? list.error} />
        <Table head={["Device", "Signed in", "Last active", ""]} empty={list.loading ? "Loading…" : "No active sessions."}>
          {list.data?.map((s) => (
            <tr key={s.id}>
              <Td><div className="max-w-md truncate" title={s.device}>{s.device}</div>{s.current && <Badge kind="good">this device</Badge>}</Td>
              <Td>{dateTime(s.created_at)}</Td>
              <Td>{s.last_refresh ? dateTime(s.last_refresh) : "–"}</Td>
              <Td>{!s.current && <Button variant="secondary" onClick={() => out(s.id)} disabled={act.busy}>Sign out</Button>}</Td>
            </tr>
          ))}
        </Table>
        <p className="mt-2 text-xs text-muted">Signing out a device stops it at its next request. Use &quot;Sign out everywhere&quot; in the header if
          you think someone else has your password.</p>
      </Card>
    </Shell>
  );
}
