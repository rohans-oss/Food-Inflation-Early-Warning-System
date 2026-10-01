"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { num } from "@/lib/format";

import { MapView } from "./MapView";
import { Button, Card, ErrorNote, Field, inputCls, Table, Td, useAction, useApi } from "./ui";

const DEFAULT_POINT: [number, number] = [13.2, 78.02]; // Kolar belt

type Member = { id: number; name: string; phone: string | null; lots: number; waiting: number; has_login: boolean };

/** FPO desk: the member farmers, and adding one (many members don't use apps; the FPO acts for them). */
export function MembersCard({ onChanged }: { onChanged?: () => void }) {
  const members = useApi<Member[]>("/fpo/members");
  const act = useAction();
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", phone: "" });
  const add = (e: React.FormEvent) => {
    e.preventDefault();
    act.run(async () => {
      await api("/fpo/members", { method: "POST", body: { name: f.name, phone: f.phone || null } });
      setF({ name: "", phone: "" });
      setAdding(false);
      members.reload();
      onChanged?.();
    });
  };
  return (
    <Card title={`Members${members.data ? ` (${members.data.length})` : ""}`}
      action={<Button variant={adding ? "secondary" : "primary"} onClick={() => setAdding(!adding)}>{adding ? "Close" : "+ Add member"}</Button>}>
      {adding && (
        <form onSubmit={add} className="mb-4 grid gap-3 rounded-xl border border-line p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field label="Farmer's name"><input className={inputCls} required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Phone (optional)"><input className={inputCls} inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Button type="submit" disabled={act.busy}>Add</Button>
        </form>
      )}
      <ErrorNote error={act.error ?? members.error} />
      <Table head={["Member", "Phone", "Lots", "Waiting to ship", "App login"]}
        empty="No members yet. Add the farmers your FPO collects from, then register their harvests.">
        {members.data?.map((m) => (
          <tr key={m.id}>
            <Td className="font-medium">{m.name}</Td>
            <Td className="text-ink2">{m.phone ?? "–"}</Td>
            <Td>{m.lots}</Td>
            <Td>{m.waiting || "–"}</Td>
            <Td className="text-xs text-muted">{m.has_login ? "uses the app" : "FPO registers for them"}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

/** Register a harvest lot on a member's behalf. */
export function AddMemberLot({ onDone }: { onDone: () => void }) {
  const members = useApi<Member[]>("/fpo/members");
  const crops = useApi<{ name: string }[]>("/crops");
  const act = useAction();
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [f, setF] = useState({ farmer_id: "", crop: "Tomato", quantity_tons: "2", grade: "Local", pickup_label: "" });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!point) return act.setError("Click the map to set the pickup point (the member's farm).");
    act.run(async () => {
      await api("/fpo/lots", { method: "POST", body: { ...f, farmer_id: Number(f.farmer_id), quantity_tons: Number(f.quantity_tons),
        pickup_lat: point[0], pickup_lon: point[1] } });
      onDone();
    });
  };
  return (
    <form onSubmit={submit} className="mb-4 space-y-3 rounded-xl border border-brand/40 bg-brand/5 p-4">
      <p className="font-semibold">Add a lot for a member</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Member">
          <select className={inputCls} required value={f.farmer_id} onChange={(e) => setF({ ...f, farmer_id: e.target.value })}>
            <option value="">Choose…</option>
            {members.data?.map((m) => <option key={m.id} value={m.id}>{m.name}{m.phone ? ` · ${m.phone}` : ""}</option>)}
          </select>
        </Field>
        <Field label="Vegetable">
          <select className={inputCls} value={f.crop} onChange={(e) => setF({ ...f, crop: e.target.value })}>
            {(crops.data ?? [{ name: "Tomato" }]).map((c) => <option key={c.name}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Quantity (tonnes)">
          <input className={inputCls} type="number" min="0.1" max="60" step="0.1" required value={f.quantity_tons}
            onChange={(e) => setF({ ...f, quantity_tons: e.target.value })} />
        </Field>
        <Field label="Grade">
          <select className={inputCls} value={f.grade} onChange={(e) => setF({ ...f, grade: e.target.value })}>
            {["Local", "Small", "Medium", "Large", "FAQ"].map((g) => <option key={g}>{g}</option>)}
          </select>
        </Field>
        <Field label="Village / farm">
          <input className={inputCls} placeholder="e.g. Vemagal" value={f.pickup_label} onChange={(e) => setF({ ...f, pickup_label: e.target.value })} />
        </Field>
      </div>
      <div>
        <p className="mb-1 text-sm text-ink2">Pickup point: {point ? `${num(point[0], 4)}, ${num(point[1], 4)}` : "click the member's farm on the map"}</p>
        <MapView height="h-56" center={[DEFAULT_POINT[1], DEFAULT_POINT[0]]} zoom={9} fitKey={point ? point.join() : "init"}
          onClick={(lat, lon) => setPoint([lat, lon])}
          markers={point ? [{ id: "p", lat: point[0], lon: point[1], kind: "pickup", label: "Pickup" }] : []} />
      </div>
      <ErrorNote error={act.error} />
      {members.data?.length === 0 && <p className="text-sm text-muted">Add a member first (Members, below).</p>}
      <Button type="submit" disabled={act.busy || !f.farmer_id}>Register lot</Button>
    </form>
  );
}
