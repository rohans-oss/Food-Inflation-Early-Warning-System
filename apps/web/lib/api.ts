// Thin client for the FastAPI backend. The token lives in localStorage (V1); every call is JWT-authenticated
// except the public tracking link.

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const WS_URL = API_URL.replace(/^http/, "ws");

const TOKEN_KEY = "ap_token";
const USER_KEY = "ap_user";

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
  preferred_lang: "en" | "kn";
  watch_mandi_ids: number[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function saveSession(token: string, user: User) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function saveUser(user: User) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e))).join("; ");
  }
  return fallback;
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = opts.auth === false ? null : getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(API_URL + path, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  const body = await r.json().catch(() => null);
  if (r.status === 401 && opts.auth !== false) {
    clearSession();
    if (typeof window !== "undefined" && !location.pathname.startsWith("/login")) location.href = "/login";
  }
  if (!r.ok) throw new ApiError(r.status, detail(body, r.statusText));
  return body as T;
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

// ------------------------------------------------------------------ response shapes used across screens

export interface Mandi {
  id: number;
  name: string;
  district: string;
  state: string;
  lat: number | null;
  lon: number | null;
  coords_verified: boolean;
}

export interface Horizon {
  weeks: number;
  target_date: string;
  p10: number;
  p50: number;
  p90: number;
}

export interface ForecastBlock {
  mandi_id: number;
  issue_date: string;
  model: string;
  model_version: string;
  trained_on_synthetic: boolean;
  spike_prob_14d: number | null;
  unit: string;
  horizons: Horizon[];
}

export interface LatestPrice {
  mandi: Mandi;
  date: string;
  modal_price: number;
  min_price: number | null;
  max_price: number | null;
  is_synthetic: boolean;
  distance_km: number | null;
}

export interface TripSummary {
  id: number;
  status: string;
  vehicle: string;
  eta_at: string | null;
  remaining_km: number | null;
  is_simulated: boolean;
  share_url?: string;
  pickup_qr_token?: string;
}

export interface Lot {
  id: number;
  crop: string;
  quantity_tons: number;
  grade: string;
  pickup_label: string;
  pickup_lat: number;
  pickup_lon: number;
  status: string;
  shipment_id: number | null;
  mandi: string | null;
  mandi_id: number | null;
  delivered_weight_kg: number | null;
  sale_price_per_quintal: number | null;
  payout_status: string;
  delivered_at: string | null;
  created_at: string;
  is_simulated: boolean;
  farmer: { id: number; name: string };
  fpo_org_id: number | null;
  lender_org_id: number | null;
  trip: TripSummary | null;
}

export interface LiveTrip {
  trip_id: number;
  status: string;
  lat: number | null;
  lon: number | null;
  speed_kmph: number | null;
  last_seen_at: string | null;
  remaining_km: number | null;
  eta_at: string | null;
  eta_local: string | null;
  vehicle: string | null;
  load_tons: number;
  is_simulated: boolean;
  stopped_since: string | null;
}

export interface TripDetail extends LiveTrip {
  id: number;
  shipment_id: number | null;
  mandi: string;
  mandi_lat: number;
  mandi_lon: number;
  mandi_geofence_m: number;
  pickup_radius_m: number;
  origin_lat: number;
  origin_lon: number;
  driver: { id: number; name: string } | null;
  started_at: string | null;
  ended_at: string | null;
  pickup_scanned_at: string | null;
  delivery_scanned_at: string | null;
  planned_distance_km: number | null;
  planned_duration_min: number | null;
  route_source: string | null;
  tracking_on: boolean;
  share_url?: string;
  route?: [number, number][] | null; // [[lon, lat], ...]
  events?: { event: string; at: string; lat: number | null; lon: number | null; details: unknown }[];
  track?: [number, number][];
  points_count?: number;
}
