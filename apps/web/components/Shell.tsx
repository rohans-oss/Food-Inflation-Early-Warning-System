"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";

import { api, Role, ROLE_HOME, wsUrl } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Alert {
  id: number;
  kind: string;
  severity: string;
  title: string;
  body: string;
  created_at: string;
  read_at: string | null;
}

/** Page frame for signed-in role screens. Redirects to /login (or the user's own home) if the role doesn't match. */
export function Shell({ roles, title, children, wide = false }: { roles: Role[]; title: string; children: ReactNode; wide?: boolean }) {
  const { user, ready, t, lang, setLang, logout, logoutAll, session } = useSession();
  const [confirmAll, setConfirmAll] = useState(false);
  const router = useRouter();
  const allowed = !!user && roles.includes(user.role);

  useEffect(() => {
    if (!ready) return;
    if (!user) router.replace("/login");
    else if (!roles.includes(user.role)) router.replace(ROLE_HOME[user.role]);
  }, [ready, user, roles, router]);

  if (!ready || !allowed) return <div className="p-8 text-sm text-muted">{t("loading")}</div>;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className={`mx-auto flex items-center gap-3 px-4 py-3 ${wide ? "max-w-[1400px]" : "max-w-6xl"}`}>
          <Link href={ROLE_HOME[user!.role]} className="flex items-center gap-2 font-semibold">
            <span aria-hidden className="grid h-7 w-7 place-items-center rounded-lg bg-brand text-sm text-brand-ink">A</span>
            <span className="hidden sm:inline">{t("app")}</span>
          </Link>
          <span className="truncate text-sm text-ink2">
            {user!.role_label}{user!.org_name ? ` · ${user!.org_name}` : ""}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <select
              aria-label={t("language")}
              value={lang}
              onChange={(e) => setLang(e.target.value as "en" | "kn" | "hi")}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="en">English</option>
              <option value="kn">ಕನ್ನಡ</option>
              <option value="hi">हिन्दी</option>
            </select>
            <AlertsBell token={session!.access_token} />
            <button onClick={async () => { await logout(); router.replace("/login"); }} className="text-sm text-ink2 hover:text-ink">
              {t("signOut")}
            </button>
            {/* B-3: self-service, for a lost phone or a password someone else may know. Second click confirms. */}
            <button
              onClick={async () => { if (!confirmAll) { setConfirmAll(true); setTimeout(() => setConfirmAll(false), 5000); return; }
                await logoutAll(); router.replace("/login"); }}
              className={`text-sm ${confirmAll ? "font-semibold text-critical" : "text-ink2 hover:text-ink"}`}
            >
              {confirmAll ? t("signOutAllConfirm") : t("signOutAll")}
            </button>
          </div>
        </div>
      </header>
      <main className={`mx-auto space-y-4 px-4 py-6 ${wide ? "max-w-[1400px]" : "max-w-6xl"}`}>
        <h1 className="text-xl font-semibold">{title}</h1>
        {children}
      </main>
    </div>
  );
}

function AlertsBell({ token }: { token: string }) {
  const { t } = useSession();
  const [open, setOpen] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [flash, setFlash] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const load = () => api<Alert[]>("/alerts").then(setAlerts).catch(() => {});
  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    // live alerts over the role-scoped websocket
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl("/ws/live", { token }));
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type === "alert") {
          setFlash(msg.title);
          setTimeout(() => setFlash(null), 6000);
          load();
        }
      };
    } catch { /* no live alerts; polling still works */ }
    return () => { clearInterval(id); ws?.close(); };
  }, [token]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const unread = alerts.filter((a) => !a.read_at).length;
  const markRead = async (id: number) => {
    await api(`/alerts/${id}/read`, { method: "POST" }).catch(() => {});
    load();
  };

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} className="relative rounded-md border border-line px-2 py-1 text-sm" aria-label={t("alerts")}>
        {t("alerts")}
        {unread > 0 && <span className="ml-1 rounded-full bg-critical px-1.5 text-xs font-semibold text-white">{unread}</span>}
      </button>
      {flash && !open && (
        <div role="status" className="absolute right-0 top-10 w-72 rounded-lg border border-line bg-surface p-3 text-sm shadow-lg">{flash}</div>
      )}
      {open && (
        <div className="absolute right-0 top-10 max-h-[70vh] w-[min(24rem,90vw)] overflow-y-auto rounded-xl border border-line bg-surface p-2 shadow-xl">
          {alerts.length === 0 && <p className="p-3 text-sm text-muted">{t("noAlerts")}</p>}
          {alerts.map((a) => (
            <div key={a.id} className={`rounded-lg p-3 text-sm ${a.read_at ? "opacity-60" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <b>{a.title}</b>
                {!a.read_at && <button onClick={() => markRead(a.id)} className="shrink-0 text-xs text-ink2 underline">{t("markRead")}</button>}
              </div>
              <p className="mt-1 text-ink2">{a.body}</p>
              <p className="mt-1 text-xs text-muted">{ago(a.created_at)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
