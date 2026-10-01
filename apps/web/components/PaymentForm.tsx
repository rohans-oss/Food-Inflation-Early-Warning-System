"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { inr } from "@/lib/format";

import { Button, ErrorNote, Field, inputCls, useAction } from "./ui";

const METHODS: [string, string][] = [["bank", "Bank transfer"], ["upi", "UPI"], ["cash", "Cash"], ["other", "Other"]];

/** The mandi records how it paid the farmer. Bank: account holder, account number, IFSC, bank, branch (only the last 4
 * digits of the account number are kept). UPI: the farmer's UPI ID. The money moves outside AgriPulse. */
export function PaymentForm({ lot, onDone, onCancel }: {
  lot: { lot_id: number; farmer: string; amount: number }; onDone: () => void; onCancel: () => void;
}) {
  const act = useAction();
  const [method, setMethod] = useState("bank");
  const [f, setF] = useState({ account_holder: lot.farmer, account_number: "", account_number2: "", ifsc: "", bank_name: "",
    branch: "", upi_id: "", reference: "", note: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const mismatch = method === "bank" && f.account_number2 !== "" && f.account_number !== f.account_number2;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    const body: Record<string, unknown> = { method, reference: f.reference };
    if (method === "bank") Object.assign(body, { account_holder: f.account_holder, account_number: f.account_number,
      ifsc: f.ifsc, bank_name: f.bank_name, branch: f.branch });
    if (method === "upi") body.upi_id = f.upi_id;
    if (method === "cash" || method === "other") body.note = f.note;
    act.run(async () => { await api(`/trader/lots/${lot.lot_id}/payment`, { method: "POST", body }); onDone(); });
  };

  return (
    <form onSubmit={submit} className="mt-4 space-y-4 rounded-xl border border-brand/40 bg-brand/5 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold">Record payment · lot #{lot.lot_id} · {lot.farmer}</p>
        <p className="text-sm">Amount <b>{inr(lot.amount)}</b></p>
      </div>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Payment method">
        {METHODS.map(([k, label]) => (
          <button key={k} type="button" role="radio" aria-checked={method === k} onClick={() => setMethod(k)}
            className={`rounded-lg border px-3 py-1.5 text-sm ${method === k ? "border-brand bg-brand text-brand-ink" : "border-line bg-surface hover:border-brand"}`}>
            {label}
          </button>
        ))}
      </div>

      {method === "bank" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Account holder name"><input className={inputCls} required value={f.account_holder} onChange={set("account_holder")} /></Field>
          <Field label="Bank name"><input className={inputCls} required placeholder="e.g. State Bank of India" value={f.bank_name} onChange={set("bank_name")} /></Field>
          <Field label="Account number"><input className={inputCls} required inputMode="numeric" autoComplete="off" value={f.account_number}
            onChange={(e) => setF({ ...f, account_number: e.target.value.replace(/[^\d]/g, "").slice(0, 18) })} /></Field>
          <Field label="Re-enter account number" hint={mismatch ? "The two numbers don't match" : undefined}>
            <input className={inputCls} required inputMode="numeric" autoComplete="off" value={f.account_number2}
              onChange={(e) => setF({ ...f, account_number2: e.target.value.replace(/[^\d]/g, "").slice(0, 18) })} /></Field>
          <Field label="IFSC code"><input className={`${inputCls} uppercase`} required placeholder="SBIN0001234" maxLength={11} value={f.ifsc}
            onChange={(e) => setF({ ...f, ifsc: e.target.value.toUpperCase() })} /></Field>
          <Field label="Branch"><input className={inputCls} required placeholder="e.g. Chintamani" value={f.branch} onChange={set("branch")} /></Field>
          <Field label="UTR / transaction reference (optional)"><input className={inputCls} value={f.reference} onChange={set("reference")} /></Field>
          <p className="self-end text-xs text-muted">Only the last 4 digits of the account number are saved.</p>
        </div>
      )}
      {method === "upi" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Farmer's UPI ID"><input className={inputCls} required placeholder="name@bank" value={f.upi_id} onChange={set("upi_id")} /></Field>
          <Field label="UPI transaction ID (optional)"><input className={inputCls} value={f.reference} onChange={set("reference")} /></Field>
        </div>
      )}
      {(method === "cash" || method === "other") && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={method === "cash" ? "Cash receipt / voucher no. (optional)" : "Reference (optional)"}><input className={inputCls} value={f.reference} onChange={set("reference")} /></Field>
          <Field label="Note (optional)"><input className={inputCls} placeholder={method === "cash" ? "e.g. paid at the mandi counter" : "how it was paid"} value={f.note} onChange={set("note")} /></Field>
        </div>
      )}

      <ErrorNote error={act.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={act.busy || mismatch}>Save payment</Button>
        <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
      <p className="text-xs text-muted">This records how the farmer was paid; the money itself moves outside AgriPulse.</p>
    </form>
  );
}
