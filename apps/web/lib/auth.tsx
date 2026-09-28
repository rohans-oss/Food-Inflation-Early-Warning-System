"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, clearSession, getStoredUser, getToken, ROLE_HOME, saveSession, saveUser, type Role, type User } from "./api";

interface AuthCtx {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<User>;
  setSession: (token: string, user: User) => void;
  updateUser: (u: User) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = getStoredUser();
    if (stored && getToken()) {
      setUser(stored);
      // Refresh in the background so role / org changes by an admin show up.
      api<User>("/auth/me")
        .then((u) => {
          saveUser(u);
          setUser(u);
        })
        .catch(() => undefined);
    }
    setReady(true);
  }, []);

  const setSession = useCallback((token: string, u: User) => {
    saveSession(token, u);
    setUser(u);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api<{ access_token: string; user: User }>("/auth/login", { body: { email, password }, auth: false });
    saveSession(r.access_token, r.user);
    setUser(r.user);
    return r.user;
  }, []);

  const updateUser = useCallback((u: User) => {
    saveUser(u);
    setUser(u);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    location.href = "/login";
  }, []);

  return <Ctx.Provider value={{ user, ready, login, setSession, updateUser, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

/** Guard a screen: redirects to /login when signed out, or to the user's own home when the role doesn't match. */
export function useRequireRole(roles: Role[]): User | null {
  const { user, ready } = useAuth();
  const router = useRouter();
  const allowed = !!user && (roles.includes(user.role) || user.role === "admin");
  useEffect(() => {
    if (!ready) return;
    if (!user) router.replace("/login");
    else if (!allowed) router.replace(ROLE_HOME[user.role]);
  }, [ready, user, allowed, router]);
  return allowed ? user : null;
}
