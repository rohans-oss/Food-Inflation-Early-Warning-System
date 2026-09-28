"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Btn, ErrorNote, Field, inputCls } from "@/components/ui";
import { ROLE_HOME } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// Accounts created by `python -m agripulse_api.seed --demo` (password = DEMO_PASSWORD, default agripulse-demo).
const DEMO = [
  ["tejas@demo.agripulse", "Farmer (Tejas)"],
  ["fpo@demo.agripulse", "FPO / aggregator"],
  ["driver@demo.agripulse", "Driver"],
  ["fleet@demo.agripulse", "Fleet owner"],
  ["trader@demo.agripulse", "Mandi trader"],
  ["buyer@demo.agripulse", "Bulk buyer"],
  ["policy@demo.agripulse", "Policy analyst"],
  ["lender@demo.agripulse", "Lender / insurer"],
  ["admin@agripulse.local", "Admin"],
];

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = await login(email, password);
      router.replace(ROLE_HOME[u.role]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-12 max-w-3xl px-4">
      <div className="mb-6 text-center">
        <h1 className="text-3xl font-bold text-green-800">AgriPulse</h1>
        <p className="text-slate-600">Tomato price early warning · farm-to-mandi vehicle tracking</p>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <form onSubmit={submit} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold">Sign in</h2>
          <Field label="Email"><input className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required /></Field>
          <Field label="Password"><input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></Field>
          <ErrorNote error={error} />
          <Btn type="submit" disabled={busy} className="w-full">{busy ? "Signing in…" : "Sign in"}</Btn>
          <p className="text-sm text-slate-600">New here? <Link href="/register" className="text-green-800 underline">Create an account</Link></p>
        </form>
        <div className="rounded-xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm">
          <h2 className="mb-2 font-semibold">Demo accounts</h2>
          <p className="mb-3 text-slate-600">Seeded with <code>--demo</code>. Click one to fill the form; the password is your <code>DEMO_PASSWORD</code> (default <code>agripulse-demo</code>). The admin password is <code>ADMIN_PASSWORD</code>.</p>
          <ul className="space-y-1">
            {DEMO.map(([e, label]) => (
              <li key={e}>
                <button type="button" className="text-left text-green-800 hover:underline" onClick={() => { setEmail(e); if (!e.startsWith("admin")) setPassword("agripulse-demo"); }}>
                  {label} <span className="text-slate-500">· {e}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
