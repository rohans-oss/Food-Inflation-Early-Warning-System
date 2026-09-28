"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Btn, ErrorNote, Field, inputCls } from "@/components/ui";
import { api, ApiError, ROLE_HOME, type Mandi, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";

interface RoleInfo { name: string; label: string; description: string; org_kind: string | null }

export default function RegisterPage() {
  const { setSession } = useAuth();
  const router = useRouter();
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [fleets, setFleets] = useState<{ id: number; name: string }[]>([]);
  const [mandis, setMandis] = useState<Mandi[]>([]);
  const [f, setF] = useState({ email: "", password: "", full_name: "", phone: "", role: "farmer", preferred_lang: "en", org_name: "", org_id: "", mandi_id: "" });
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    api<RoleInfo[]>("/auth/roles", { auth: false }).then((r) => setRoles(r.filter((x) => x.name !== "admin"))).catch(() => undefined);
  }, []);

  const role = roles.find((r) => r.name === f.role);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const body: Record<string, unknown> = { email: f.email, password: f.password, full_name: f.full_name, role: f.role, preferred_lang: f.preferred_lang, phone: f.phone || null };
    if (role?.org_kind) {
      if (f.role === "driver" && f.org_id) body.org_id = Number(f.org_id);
      else body.org_name = f.org_name;
    }
    if (f.role === "trader") body.mandi_id = Number(f.mandi_id);
    try {
      const r = await api<{ access_token: string; user: User }>("/auth/register", { body, auth: false });
      setSession(r.access_token, r.user);
      router.replace(ROLE_HOME[r.user.role]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 202) setInfo(err.message);
      else setError((err as Error).message);
    }
  }

  useEffect(() => {
    api<{ mandis: Mandi[]; fleets: { id: number; name: string }[] }>("/auth/signup-options", { auth: false })
      .then((o) => { setMandis(o.mandis); setFleets(o.fleets); })
      .catch(() => undefined);
  }, []);

  return (
    <div className="mx-auto mt-10 max-w-lg px-4">
      <form onSubmit={submit} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h1 className="text-lg font-semibold">Create an AgriPulse account</h1>
        <Field label="I am a">
          <select className={inputCls} value={f.role} onChange={set("role")}>
            {roles.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
          </select>
        </Field>
        {role && <p className="text-xs text-slate-500">{role.description}</p>}
        <Field label="Full name"><input className={inputCls} value={f.full_name} onChange={set("full_name")} required /></Field>
        <Field label="Email"><input className={inputCls} type="email" value={f.email} onChange={set("email")} required /></Field>
        <Field label="Phone (for SMS alerts, optional)"><input className={inputCls} value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Password (8+ characters)"><input className={inputCls} type="password" minLength={8} value={f.password} onChange={set("password")} required /></Field>
        <Field label="Language">
          <select className={inputCls} value={f.preferred_lang} onChange={set("preferred_lang")}>
            <option value="en">English</option>
            <option value="kn">ಕನ್ನಡ (Kannada)</option>
          </select>
        </Field>
        {role?.org_kind && f.role === "driver" && (
          <Field label="Join a fleet (your fleet owner approves you)">
            {fleets.length ? (
              <select className={inputCls} value={f.org_id} onChange={set("org_id")}>
                <option value="">— start my own fleet instead —</option>
                {fleets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            ) : (
              <input className={inputCls} placeholder="Fleet id (ask your fleet owner)" value={f.org_id} onChange={set("org_id")} />
            )}
          </Field>
        )}
        {role?.org_kind && !(f.role === "driver" && f.org_id) && (
          <Field label={`New ${role.org_kind === "government" ? "department" : role.org_kind} name`}>
            <input className={inputCls} value={f.org_name} onChange={set("org_name")} required />
          </Field>
        )}
        {f.role === "trader" && (
          <Field label="Your mandi">
            {mandis.length ? (
              <select className={inputCls} value={f.mandi_id} onChange={set("mandi_id")} required>
                <option value="">Choose…</option>
                {mandis.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.district})</option>)}
              </select>
            ) : (
              <input className={inputCls} placeholder="Mandi id (ask an admin)" value={f.mandi_id} onChange={set("mandi_id")} required />
            )}
          </Field>
        )}
        <ErrorNote error={error} />
        {info && <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">{info}</div>}
        <Btn type="submit" className="w-full">Create account</Btn>
        <p className="text-sm text-slate-600">Already registered? <Link href="/login" className="text-green-800 underline">Sign in</Link></p>
      </form>
    </div>
  );
}
