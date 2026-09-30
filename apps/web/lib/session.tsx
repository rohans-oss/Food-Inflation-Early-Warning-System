"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import messages from "./messages.json";
import { api, getSession, Session, setSession, User } from "./api";

type Lang = "en" | "kn" | "hi";
type Msgs = Record<string, string>;
const M = messages as unknown as Record<Lang, Msgs>;

interface Ctx {
  session: Session | null;
  ready: boolean;
  user: User | null;
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  reloadUser: () => Promise<void>;
}

const SessionCtx = createContext<Ctx | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setS] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [lang, setLangState] = useState<Lang>("en");

  useEffect(() => {
    const sync = () => {
      const s = getSession();
      setS(s);
      if (s?.user.preferred_lang) setLangState(s.user.preferred_lang);
    };
    sync();
    setReady(true);
    window.addEventListener("agripulse-session", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("agripulse-session", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const t = useCallback((key: string) => M[lang]?.[key] ?? M.en[key] ?? key, [lang]);

  const reloadUser = useCallback(async () => {
    const s = getSession();
    if (!s) return;
    const user = await api<User>("/auth/me");
    setSession({ ...s, user });
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (getSession()) api("/auth/me", { method: "PATCH", body: { preferred_lang: l } }).then(reloadUser).catch(() => {});
  }, [reloadUser]);

  // B-3: end the session on the server too, so a copied token stops working. Best effort: offline still signs out here.
  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    setSession(null);
  }, []);
  const logoutAll = useCallback(async () => {
    await api("/auth/logout-all", { method: "POST", body: {} }).catch(() => {});
    setSession(null);
  }, []);

  return (
    <SessionCtx.Provider value={{ session, ready, user: session?.user ?? null, lang, setLang, t, logout, logoutAll, reloadUser }}>
      {children}
    </SessionCtx.Provider>
  );
}

export function useSession(): Ctx {
  const c = useContext(SessionCtx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
}
