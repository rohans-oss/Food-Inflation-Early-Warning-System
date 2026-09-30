"use client";

// API base: NEXT_PUBLIC_API_URL at build time, otherwise the same host on port 8000
// (so a phone on the LAN opening http://<laptop-ip>:3000 talks to http://<laptop-ip>:8000).
export function apiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env) return env.replace(/\/$/, "");
  if (typeof window === "undefined") return "http://localhost:8000";
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

export type Role =
  | "farmer" | "fpo" | "driver" | "fleet_owner" | "trader" | "buyer" | "policy" | "lender" | "admin";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  role_label: string;
  org_id: number | null;
  org_name: string | null;
  mandi_id: number | null;
  preferred_lang: "en" | "kn" | "hi";
  watch_mandi_ids: number[];
}

export interface Session {
  access_token: string;
  refresh_token: string;
  user: User;
}

const KEY = "agripulse_session";

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function setSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: session lives only in memory for this tab */
  }
  window.dispatchEvent(new Event("agripulse-session"));
}

export const ROLE_HOME: Record<Role, string> = {
  farmer: "/farmer",
  fpo: "/fpo",
  driver: "/driver",
  fleet_owner: "/fleet",
  trader: "/trader",
  buyer: "/buyer",
  policy: "/policy",
  lender: "/lender",
  admin: "/admin",
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const s = getSession();
  if (!s?.refresh_token) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch(apiBase() + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: s.refresh_token }),
      });
      if (!r.ok) return false;
      setSession(await r.json());
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown; auth?: boolean; query?: Record<string, unknown> } = {},
  retried = false,
): Promise<T> {
  const { method = "GET", body, auth = true, query } = opts;
  let url = apiBase() + path;
  if (query) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    if ([...q].length) url += "?" + q.toString();
  }
  const s = getSession();
  const r = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(auth && s ? { Authorization: `Bearer ${s.access_token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && auth && !retried && (await refresh())) return api<T>(path, opts, true);
  if (r.status === 401 && auth) {
    setSession(null);
    throw new ApiError(401, "Session expired, please sign in again");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = (data as any)?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: any) => d.msg).join("; ") : r.statusText;
    throw new ApiError(r.status, msg);
  }
  return data as T;
}

export function wsUrl(path: string, params: Record<string, string>): string {
  const q = new URLSearchParams(params).toString();
  return apiBase().replace(/^http/, "ws") + path + (q ? "?" + q : "");
}
