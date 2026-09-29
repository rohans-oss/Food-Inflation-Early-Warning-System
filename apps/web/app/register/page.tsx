"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, ErrorNote, Field, inputCls } from "@/components/ui";
import { api, ROLE_HOME, Session, setSession } from "@/lib/api";

interface RoleInfo { name: string; label: string; description: string; org_kind: string | null }

export default function Register() {
  const router = useRouter();
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [mandis, setMandis] = useState<{ id: number; name: string }[]>([]);
  const [fleets, setFleets] = useState<{ id: number; name: string }[]>([]);
  const [f, setF] = useState({ email: "", password: "", full_name: "", phone: "", role: "farmer", preferred_lang: "en",
    org_name: "", org_id: "", mandi_id: "" });
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    api<RoleInfo[]>("/auth/roles", { auth: false }).then((r) => setRoles(r.filter((x) => x.name !== "admin"))).catch(() => {});
  }, []);
  const role = roles.find((r) => r.name === f.role);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const body: Record<string, unknown> = { email: f.email, password: f.password, full_name: f.full_name, role: f.role,
      preferred_lang: f.preferred_lang, phone: f.phone || null };
    if (role?.org_kind) {
      if (f.role === "driver" && f.org_id) body.org_id = Number(f.org_id);
      else body.org_name = f.org_name;
    }
    if (f.role === "trader") body.mandi_id = Number(f.mandi_id);
    try {
      const s = await api<Session>("/auth/register", { method: "POST", body, auth: false });
      setSession(s);
      router.replace(ROLE_HOME[s.user.role]);
    } catch (e: any) {
      if (e.status === 202) setDone(e.message);
      else setErr(e.message);
    }
  };

  useEffect(() => {
    // public lookups (names only)
    api<{ id: number; name: string }[]>("/mandis", { auth: false }).then(setMandis).catch(() => setMandis([]));
    api<{ id: number; name: string }[]>("/orgs/directory", { query: { kind: "fleet" }, auth: false }).then(setFleets).catch(() => setFleets([]));
  }, []);

  if (done) return <main className="mx-auto max-w-md p-8"><p>{done}</p><Link href="/login" className="underline">Back to sign in</Link></main>;

  return (
    <main className="mx-auto max-w-lg px-4 py-10">
      <h1 className="mb-4 text-xl font-semibold">Create an account</h1>
      <form onSubmit={submit} className="space-y-3">
        <Field label="I am a">
          <select className={inputCls} value={f.role} onChange={set("role")}>
            {roles.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
          </select>
        </Field>
        {role && <p className="text-xs text-muted">{role.description}</p>}
        <Field label="Full name"><input className={inputCls} required value={f.full_name} onChange={set("full_name")} /></Field>
        <Field label="Email"><input className={inputCls} type="email" required value={f.email} onChange={set("email")} /></Field>
        <Field label="Phone (for SMS alerts, optional)"><input className={inputCls} value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Password" hint="At least 8 characters"><input className={inputCls} type="password" minLength={8} required value={f.password} onChange={set("password")} /></Field>
        <Field label="Language for alerts">
          <select className={inputCls} value={f.preferred_lang} onChange={set("preferred_lang")}>
            <option value="en">English</option><option value="kn">ಕನ್ನಡ (Kannada)</option>
          </select>
        </Field>
        {role?.org_kind && f.role === "driver" && (
          <Field label="Fleet" hint="Your fleet owner approves you before you can sign in">
            {fleets.length ? (
              <select className={inputCls} value={f.org_id} onChange={set("org_id")} required>
                <option value="">Choose…</option>
                {fleets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            ) : <input className={inputCls} placeholder="Fleet id (ask your fleet owner)" value={f.org_id} onChange={set("org_id")} required />}
          </Field>
        )}
        {role?.org_kind && f.role !== "driver" && (
          <Field label={`New ${role.org_kind === "government" ? "department" : role.org_kind} name`} hint="You'll be its first member. To join an existing one, ask an admin.">
            <input className={inputCls} required value={f.org_name} onChange={set("org_name")} />
          </Field>
        )}
        {f.role === "trader" && (
          <Field label="Your mandi">
            {mandis.length ? (
              <select className={inputCls} required value={f.mandi_id} onChange={set("mandi_id")}>
                <option value="">Choose…</option>
                {mandis.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            ) : <input className={inputCls} required placeholder="Mandi id (ask an admin)" value={f.mandi_id} onChange={set("mandi_id")} />}
          </Field>
        )}
        <ErrorNote error={err} />
        <Button type="submit" className="w-full">Create account</Button>
      </form>
      <p className="mt-4 text-sm text-ink2">Have an account? <Link href="/login" className="underline">Sign in</Link></p>
    </main>
  );
}
