"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/Logo";
import { ServerNotice, useServerReady } from "@/components/ServerWake";
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
  // the public demo disables admin (docs/deployment.md), so it isn't offered there
  ...(process.env.NEXT_PUBLIC_DEMO_NOTICE ? [] : [["Admin", "admin@agripulse.local"]]),
];

export default function Login() {
  const router = useRouter();
  const { user, ready } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const server = useServerReady();

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
      setErr(e instanceof TypeError || /fetch/i.test(e.message ?? "")
        ? "Can't reach the server yet. It may still be starting; try again in a moment." : e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto grid min-h-screen max-w-4xl items-center gap-8 px-4 py-10 md:grid-cols-2">
      <div>
        <Link href="/" className="mb-6 inline-block"><Logo /></Link>
        <p className="mb-6 text-sm text-ink2">Tomato price early warning, and live tracking of produce from farm to mandi.</p>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Email"><input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></Field>
          <Field label="Password"><input className={inputCls} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
          <ServerNotice state={server} />
          <ErrorNote error={err} />
          <Button type="submit" disabled={busy || server === "waking" || server === "checking"} className="w-full">
            {busy ? "Signing in…" : server === "waking" || server === "checking" ? "Waiting for the server…" : "Sign in"}
          </Button>
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
