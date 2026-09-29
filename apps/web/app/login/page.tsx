"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, ErrorNote, Field, inputCls } from "@/components/ui";
import { api, ROLE_HOME, Session, setSession } from "@/lib/api";
import { useSession } from "@/lib/session";

// Created by `python -m agripulse_api.seed --demo`. Password: DEMO_PASSWORD (default agripulse-demo).
const DEMO = [
  ["Farmer (Tejas)", "tejas@demo.agripulse"],
  ["FPO / aggregator", "fpo@demo.agripulse"],
  ["Driver", "driver@demo.agripulse"],
  ["Fleet owner", "fleet@demo.agripulse"],
  ["Mandi trader", "trader@demo.agripulse"],
  ["Bulk buyer", "buyer@demo.agripulse"],
  ["Policy analyst", "policy@demo.agripulse"],
  ["Lender / insurer", "lender@demo.agripulse"],
  ["Admin", "admin@agripulse.local"],
];

export default function Login() {
  const router = useRouter();
  const { user, ready } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (ready && user) router.replace(ROLE_HOME[user.role]); }, [ready, user, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const s = await api<Session>("/auth/login", { method: "POST", body: { email, password }, auth: false });
      setSession(s);
      router.replace(ROLE_HOME[s.user.role]);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto grid min-h-screen max-w-4xl items-center gap-8 px-4 py-10 md:grid-cols-2">
      <div>
        <div className="mb-6 flex items-center gap-2 text-lg font-semibold">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand text-brand-ink">A</span> AgriPulse
        </div>
        <p className="mb-6 text-sm text-ink2">Tomato price early warning, and live tracking of produce from farm to mandi.</p>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Email"><input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></Field>
          <Field label="Password"><input className={inputCls} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
          <ErrorNote error={err} />
          <Button type="submit" disabled={busy} className="w-full">{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
        <p className="mt-4 text-sm text-ink2">New here? <Link href="/register" className="underline">Create an account</Link></p>
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <h2 className="mb-1 font-semibold">Demo accounts</h2>
        <p className="mb-3 text-xs text-muted">Seeded with <code>--demo</code>. Password is <code>agripulse-demo</code> unless you changed DEMO_PASSWORD (admin uses ADMIN_PASSWORD).</p>
        <ul className="grid gap-1 text-sm">
          {DEMO.map(([label, em]) => (
            <li key={em}>
              <button onClick={() => { setEmail(em); setPassword(em.startsWith("admin") ? "" : "agripulse-demo"); }}
                className="flex w-full justify-between rounded-md px-2 py-1.5 text-left hover:bg-page">
                <span>{label}</span><span className="text-muted">{em}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
