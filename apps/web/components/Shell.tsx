"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useState } from "react";
import { api, ROLE_HOME, type Role, type User } from "@/lib/api";
import { useAuth, useRequireRole } from "@/lib/auth";
import { t } from "@/lib/i18n";
import { useLive, usePoll } from "@/lib/live";

const ADMIN_LINKS: { href: string; label: string }[] = [
  { href: "/admin", label: "Admin" },
  { href: "/farmer", label: "Farmer" },
  { href: "/fpo", label: "FPO" },
  { href: "/fleet", label: "Fleet" },
  { href: "/trader", label: "Trader" },
  { href: "/buyer", label: "Buyer" },
  { href: "/policy", label: "Policy" },
  { href: "/lender", label: "Lender" },
];

/** Page frame for a signed-in role screen. Renders nothing until the role check passes. */
export default function Shell({ roles, title, children }: { roles: Role[]; title: string; children: (user: User) => React.ReactNode }) {
  const user = useRequireRole(roles);
  const { logout, updateUser } = useAuth();
  const path = usePathname();
  const [unread, setUnread] = useState(0);
  const [toast, setToast] = useState<string | null>(null);

  const refreshAlerts = useCallback(() => {
    api<unknown[]>("/alerts?unread=true").then((a) => setUnread(a.length)).catch(() => undefined);
  }, []);
  usePoll(() => user && refreshAlerts(), 60000, [user?.id]);
  useLive(user ? "/ws/live" : null, (msg) => {
    if (msg.type === "alert") {
      setUnread((n) => n + 1);
      setToast(String(msg.title || "New alert"));
      setTimeout(() => setToast(null), 6000);
    }
  });

  if (!user) return <div className="p-8 text-sm text-slate-500">Loading…</div>;

  const toggleLang = async () => {
    const u = await api<User>("/auth/me", { method: "PATCH", body: { preferred_lang: user.preferred_lang === "kn" ? "en" : "kn" } });
    updateUser(u);
  };

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
          <Link href={ROLE_HOME[user.role]} className="flex items-center gap-2 font-bold text-green-800">
            <span className="inline-block h-3 w-3 rounded-full bg-red-600" /> AgriPulse
          </Link>
          <span className="text-sm text-slate-500">{user.role_label}{user.org_name ? ` · ${user.org_name}` : ""}</span>
          {user.role === "admin" && (
            <nav className="flex flex-wrap gap-1 text-sm">
              {ADMIN_LINKS.map((l) => (
                <Link key={l.href} href={l.href} className={`rounded px-2 py-0.5 ${path === l.href ? "bg-green-100 text-green-900" : "text-slate-600 hover:bg-slate-100"}`}>
                  {l.label}
                </Link>
              ))}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-3 text-sm">
            <button onClick={toggleLang} className="rounded border border-slate-300 px-2 py-0.5" title="Language">
              {user.preferred_lang === "kn" ? "ಕನ್ನಡ → EN" : "EN → ಕನ್ನಡ"}
            </button>
            <Link href="/alerts" className="relative text-slate-700 hover:text-green-800">
              {t(user.preferred_lang, "alerts")}
              {unread > 0 && <span className="ml-1 rounded-full bg-red-600 px-1.5 text-xs text-white">{unread}</span>}
            </Link>
            <span className="hidden text-slate-500 sm:inline">{user.full_name}</span>
            <button onClick={logout} className="text-slate-600 hover:text-red-700">{t(user.preferred_lang, "logout")}</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl space-y-4 px-4 py-5">
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {children(user)}
      </main>
      {toast && (
        <div className="fixed bottom-4 right-4 max-w-sm rounded-lg bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">{toast}</div>
      )}
    </div>
  );
}
