"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { inr, num } from "@/lib/format";
import { useLiveFeed } from "@/lib/live";

import { Button, Card, ErrorNote, inputCls, useAction, useApi } from "./ui";

/** Direct farmer bookings on the driver's page: go online (district, mandis, truck), get trip requests live
 * (websocket now, Web Push when the screen is locked) and accept or decline. */
export function DriverDirect({ onAccepted }: { onAccepted: () => void }) {
  const av = useApi<any>("/driver/availability");
  const reqs = useApi<any[]>("/driver/requests", { poll: 15000 }); // also the check-in that keeps you matched
  const act = useAction();
  const [district, setDistrict] = useState<string | null>(null);
  const [mandiIds, setMandiIds] = useState<number[] | null>(null);
  const [vehicleId, setVehicleId] = useState<number | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [, tick] = useState(0);
  const known = useRef<Set<number>>(new Set());
  const a = av.data;

  useLiveFeed((m) => { if (m.type === "trip_request" || m.type === "trip_request_closed") reqs.reload(); });
  useEffect(() => {
    const onMsg = (e: MessageEvent) => { if (e.data?.type === "open_requests") reqs.reload(); };
    navigator.serviceWorker?.addEventListener("message", onMsg);
    const onVis = () => { if (document.visibilityState === "visible") reqs.reload(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { navigator.serviceWorker?.removeEventListener("message", onMsg); document.removeEventListener("visibilitychange", onVis); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { // a new request: vibrate + a short tone
    const fresh = (reqs.data ?? []).filter((r) => !known.current.has(r.request_id));
    fresh.forEach((r) => known.current.add(r.request_id));
    if (fresh.length) {
      try { navigator.vibrate?.([400, 200, 400, 200, 400]); } catch { /* not supported */ }
      beep();
      setFlash(`New trip request: ${fresh[0].crop} ${fresh[0].tons} t → ${fresh[0].mandi}`);
      setTimeout(() => setFlash(null), 8000);
    }
  }, [reqs.data]);
  useEffect(() => {
    if (!reqs.data?.length) return;
    const id = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, [reqs.data]);
  // keep the screen on while online (phones otherwise sleep and the socket closes)
  useEffect(() => {
    if (!a?.online) return;
    let lock: any = null;
    const take = async () => { try { lock = await (navigator as any).wakeLock?.request("screen"); } catch { lock = null; } };
    take();
    const onVis = () => { if (document.visibilityState === "visible") take(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { document.removeEventListener("visibilitychange", onVis); lock?.release?.().catch?.(() => {}); };
  }, [a?.online]);

  if (!a) return <ErrorNote error={av.error} />;
  if (a.demo_account) {
    return (
      <Card title="Direct bookings">
        <p className="text-sm text-muted">Farmers can book drivers directly with real accounts. Sign up with your own driver account (your fleet owner adds your phone number) to receive them.</p>
      </Card>
    );
  }
  const d = district ?? a.district ?? "";
  const chosen = mandiIds ?? a.mandi_ids;
  const vid = vehicleId ?? a.vehicle_id ?? a.vehicles[0]?.id ?? null;
  const names = a.mandis.filter((m: any) => a.mandi_ids.includes(m.id)).map((m: any) => m.name).join(", ");
  const save = (online: boolean) => act.run(async () => {
    const out = await api("/driver/availability", { method: "PUT", body: { online, district: d || null, mandi_ids: chosen, vehicle_id: vid } });
    av.setData(out);
    setDistrict(null); setMandiIds(null); setVehicleId(null);
    if (online) { primeBeep(); reqs.reload(); }
  });
  const answer = (id: number, how: "accept" | "decline") => act.run(async () => {
    await api(`/driver/requests/${id}/${how}`, { method: "POST" });
    reqs.reload();
    if (how === "accept") onAccepted();
  });

  return (
    <>
      {reqs.data?.map((r) => {
        const left = Math.max(0, Math.round((new Date(r.expires_at).getTime() - Date.now()) / 1000));
        return (
          <div key={r.request_id} className="rounded-xl border-2 border-critical bg-surface p-4 shadow-lg">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold">🚚 New trip request</p>
                <p className="text-sm text-ink2"><b className="text-ink">{r.farmer}</b>{r.village ? ` · ${r.village}` : ""}{r.farmer_district ? ` (${r.farmer_district})` : ""}</p>
              </div>
              <p className="font-mono text-lg font-semibold text-critical">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</p>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
              <div><dt className="text-muted">Produce</dt><dd>{r.crop} · {num(r.tons, 1)} t</dd></div>
              <div><dt className="text-muted">Deliver to</dt><dd>{r.mandi}</dd></div>
              <div><dt className="text-muted">Distance</dt><dd>~{num(r.road_km, 0)} km</dd></div>
              <div><dt className="text-muted">Your pay (est.)</dt><dd>{inr(r.driver_pay_estimate)}</dd></div>
            </dl>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button disabled={act.busy} onClick={() => answer(r.request_id, "accept")}>Accept</Button>
              <Button variant="secondary" disabled={act.busy} onClick={() => answer(r.request_id, "decline")}>Decline</Button>
              <a className="text-xs underline" target="_blank" rel="noreferrer"
                href={`https://www.openstreetmap.org/?mlat=${r.pickup_lat}&mlon=${r.pickup_lon}#map=14/${r.pickup_lat}/${r.pickup_lon}`}>Farm on the map</a>
            </div>
          </div>
        );
      })}
      {flash && <div role="status" className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-brand-ink">{flash}</div>}

      <Card title="Direct bookings from farmers">
        <p className={`mb-3 text-sm font-semibold ${a.online ? (a.matched_now ? "text-brand" : "text-serious") : "text-muted"}`}>
          {a.online
            ? a.matched_now ? `● Online: farmers in ${a.district} sending to ${names} can book you`
              : "● Online, but this device hasn't checked in recently. Keep this page open or turn on notifications."
            : "○ Offline: you won't get direct bookings"}
        </p>
        <ErrorNote error={act.error} />
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">District</span>
            <select className={inputCls} value={d} onChange={(e) => { setDistrict(e.target.value); setMandiIds([]); }}>
              <option value="">Choose…</option>
              {a.districts.map((x: string) => <option key={x}>{x}</option>)}
            </select>
          </label>
          <div className="text-sm">
            <span className="mb-1 block text-xs text-muted">Mandis you will deliver to</span>
            <div className="space-y-1">
              {a.mandis.filter((m: any) => m.district === d).map((m: any) => (
                <label key={m.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={chosen.includes(m.id)}
                    onChange={(e) => setMandiIds(e.target.checked ? [...chosen, m.id] : chosen.filter((x: number) => x !== m.id))} />
                  {m.name}
                </label>
              ))}
              {!d && <p className="text-xs text-muted">Choose a district first</p>}
            </div>
          </div>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Truck you are driving today</span>
            <select className={inputCls} value={vid ?? ""} onChange={(e) => setVehicleId(+e.target.value)}>
              {a.vehicles.length === 0 && <option value="">No truck in your company yet</option>}
              {a.vehicles.map((v: any) => <option key={v.id} value={v.id}>{v.registration} · {num(v.capacity_tons, 1)} t</option>)}
            </select>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {a.online ? (
            <>
              <Button disabled={act.busy} onClick={() => save(true)}>Save</Button>
              <Button variant="secondary" disabled={act.busy} onClick={() => save(false)}>Go offline</Button>
            </>
          ) : <Button disabled={act.busy} onClick={() => save(true)}>Go online</Button>}
          <PushToggle subscribed={a.push_subscribed} onChange={() => av.reload()} />
        </div>
        <p className="mt-3 text-xs text-muted">
          While online, keep this page open: new requests appear here within a second and the phone vibrates.
          With notifications on they also reach a locked phone, and you stay matched for {Math.round(a.stays_online_min / 60)} h
          {a.push_subscribed ? "" : " (without notifications: 10 minutes after you last opened the page)"}.
        </p>
      </Card>
    </>
  );
}

function PushToggle({ subscribed, onChange }: { subscribed: boolean; onChange: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (!supported) return <span className="text-xs text-muted">This browser can&apos;t show notifications: keep the page open while online.</span>;
  const on = subscribed && Notification.permission === "granted";
  const enable = async () => {
    setMsg(null);
    try {
      if (await Notification.requestPermission() !== "granted") { setMsg("Notifications are blocked in the browser's site settings."); return; }
      const reg = await navigator.serviceWorker.register("/push-sw.js");
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        const { key } = await api<{ key: string }>("/push/vapid-public-key");
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64url(key) });
      }
      await api("/push/subscribe", { method: "POST", body: sub.toJSON() });
      onChange();
    } catch (e: any) { setMsg(`Couldn't turn on notifications: ${e.message}`); }
  };
  return (
    <span className="flex items-center gap-2 text-sm">
      {on ? <span className="text-brand">🔔 Notifications on</span>
        : <Button variant="secondary" onClick={enable}>🔔 Turn on notifications</Button>}
      {msg && <span className="text-xs text-critical">{msg}</span>}
    </span>
  );
}

function b64url(s: string): Uint8Array<ArrayBuffer> {
  const raw = atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

let ctx: AudioContext | null = null;
function primeBeep() {
  try { ctx = ctx ?? new (window.AudioContext || (window as any).webkitAudioContext)(); ctx.resume?.(); } catch { ctx = null; }
}
function beep() {
  if (!ctx) return;
  try {
    for (const [at, f] of [[0, 880], [0.25, 660], [0.5, 880]]) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.25, ctx.currentTime + at);
      o.start(ctx.currentTime + at); o.stop(ctx.currentTime + at + 0.2);
    }
  } catch { /* audio blocked */ }
}
