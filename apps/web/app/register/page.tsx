"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/Logo";
import { ServerNotice, useServerReady } from "@/components/ServerWake";
import { Button, ErrorNote, Field, inputCls } from "@/components/ui";
import { api, ROLE_HOME, Session, setSession } from "@/lib/api";

interface RoleInfo { name: string; label: string; description: string; org_kind: string | null }

// Sign-up rules (2026-10-01): every role except farmer / buyer says which district it works in; mandi managers then
// pick their mandi in that district; drivers can only sign up once their fleet owner has added their phone number.
const NEEDS_DISTRICT = ["trader", "driver", "fleet_owner", "fpo", "lender", "policy"];
const DISTRICT_LABEL: Record<string, string> = {
  trader: "District of your mandi", driver: "District you are joining", fleet_owner: "District your trucks are based in",
  fpo: "District of your FPO", lender: "District you serve", policy: "District / area you cover",
};
const ORG_LABEL: Record<string, string> = {
  fpo: "FPO name", fleet_owner: "Transport company name", lender: "Bank / insurer name",
  policy: "Department name", buyer: "Company name",
};

export default function Register() {
  const router = useRouter();
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [mandis, setMandis] = useState<{ id: number; name: string; district: string }[]>([]);
  const [districts, setDistricts] = useState<string[]>([]);
  const [f, setF] = useState({ email: "", password: "", full_name: "", phone: "", role: "farmer", preferred_lang: "en",
    org_name: "", mandi_id: "", district: "", vehicle_registration: "", vehicle_capacity_tons: "5" });
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const server = useServerReady();
  const [lookupErr, setLookupErr] = useState<string | null>(null);
  useEffect(() => {
    if (server !== "ready") return;
    api<RoleInfo[]>("/auth/roles", { auth: false })
      .then((r) => { setRoles(r.filter((x) => x.name !== "admin")); setLookupErr(null); })
      .catch((e) => setLookupErr(`Could not load the list of roles: ${e.message}`));
    // public lookups (names only)
    api<{ id: number; name: string; district: string }[]>("/mandis", { auth: false }).then(setMandis).catch(() => setMandis([]));
    api<string[]>("/auth/districts", { auth: false }).then(setDistricts).catch(() => setDistricts([]));
  }, [server]);
  const role = roles.find((r) => r.name === f.role);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const body: Record<string, unknown> = { email: f.email, password: f.password, full_name: f.full_name, role: f.role,
      preferred_lang: f.preferred_lang, phone: f.phone || null, district: f.district || null };
    if (role?.org_kind && f.role !== "driver") body.org_name = f.org_name;
    if (f.role === "trader") body.mandi_id = Number(f.mandi_id);
    if (f.role === "driver") {
      body.vehicle_registration = f.vehicle_registration;
      body.vehicle_capacity_tons = Number(f.vehicle_capacity_tons) || null;
    }
    try {
      const s = await api<Session>("/auth/register", { method: "POST", body, auth: false });
      setSession(s);
      router.replace(ROLE_HOME[s.user.role]);
    } catch (e: any) {
      if (e.status === 202) setDone(e.message);
      else setErr(e.message);
    }
  };

  if (done) return <main className="mx-auto max-w-md p-8"><p>{done}</p><Link href="/login" className="underline">Back to sign in</Link></main>;

  return (
    <main className="mx-auto max-w-lg px-4 py-10">
      <Link href="/" className="mb-6 inline-block"><Logo /></Link>
      <h1 className="mb-4 text-xl font-semibold">Create an account</h1>
      <div className="mb-3 space-y-2"><ServerNotice state={server} /><ErrorNote error={lookupErr} /></div>
      <form onSubmit={submit} className="space-y-3">
        <Field label="I am a">
          <select className={inputCls} value={f.role} onChange={set("role")} disabled={!roles.length}>
            {!roles.length && <option value={f.role}>{server === "down" ? "Server unavailable" : "Loading roles…"}</option>}
            {roles.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
          </select>
        </Field>
        {role && <p className="text-xs text-muted">{role.description}</p>}
        <Field label="Full name"><input className={inputCls} required value={f.full_name} onChange={set("full_name")} /></Field>
        <Field label="Email"><input className={inputCls} type="email" required value={f.email} onChange={set("email")} /></Field>
        {f.role === "driver" ? (
          <>
            <p className="rounded-lg bg-page px-3 py-2 text-xs text-ink2">Drivers can sign up only after their fleet owner has added
              their phone number. Use that same number here.</p>
            <Field label="Mobile number (the one your fleet owner added)"><input className={inputCls} inputMode="tel" required value={f.phone} onChange={set("phone")} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Vehicle number"><input className={inputCls} required placeholder="KA-07-AB-1234" value={f.vehicle_registration} onChange={set("vehicle_registration")} /></Field>
              <Field label="Vehicle capacity (tonnes)"><input className={inputCls} type="number" min="0.5" max="60" step="0.5" value={f.vehicle_capacity_tons} onChange={set("vehicle_capacity_tons")} /></Field>
            </div>
          </>
        ) : (
          <Field label={f.role === "farmer" || f.role === "buyer" ? "Phone (for SMS alerts, optional)" : "Phone"}>
            <input className={inputCls} inputMode="tel" required={!["farmer", "buyer"].includes(f.role)} value={f.phone} onChange={set("phone")} />
          </Field>
        )}
        <Field label="Password" hint="At least 8 characters"><input className={inputCls} type="password" minLength={8} required value={f.password} onChange={set("password")} /></Field>
        <Field label="Language for alerts">
          <select className={inputCls} value={f.preferred_lang} onChange={set("preferred_lang")}>
            <option value="en">English</option><option value="kn">ಕನ್ನಡ (Kannada)</option><option value="hi">हिन्दी (Hindi)</option>
          </select>
        </Field>
        {role?.org_kind && f.role !== "driver" && (
          <Field label={ORG_LABEL[f.role] ?? "Organization name"} hint="You'll be its first member; colleagues are added from inside it.">
            <input className={inputCls} required value={f.org_name} onChange={set("org_name")} />
          </Field>
        )}
        {NEEDS_DISTRICT.includes(f.role) && (
          <Field label={DISTRICT_LABEL[f.role] ?? "District"}>
            <select className={inputCls} required value={f.district} onChange={(e) => setF({ ...f, district: e.target.value, mandi_id: "" })}>
              <option value="">Choose…</option>
              {f.role === "policy" && <option value="Karnataka (state-wide)">Karnataka (state-wide)</option>}
              {districts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Field>
        )}
        {f.role === "trader" && (
          <Field label="Your mandi" hint={f.district ? undefined : "Choose your district first"}>
            <select className={inputCls} required disabled={!f.district} value={f.mandi_id} onChange={set("mandi_id")}>
              <option value="">Choose…</option>
              {mandis.filter((m) => m.district === f.district).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
        )}
        <ErrorNote error={err} />
        <Button type="submit" className="w-full">Create account</Button>
      </form>
      <p className="mt-4 text-sm text-ink2">Have an account? <Link href="/login" className="underline">Sign in</Link></p>
    </main>
  );
}
