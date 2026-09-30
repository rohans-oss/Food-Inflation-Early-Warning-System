"use client";

import { useEffect, useRef, useState } from "react";

import { getSession, wsTicket, wsUrl } from "./api";

/** Subscribe to one trip's live stream. Auth with the session token, or a public share token. */
export function useLiveTrip(tripId: number | null | undefined, opts: { share?: string } = {}) {
  const [pos, setPos] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [connected, setConnected] = useState(false);
  const retry = useRef(0);

  useEffect(() => {
    if (!tripId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    const open = async () => {
      let params: Record<string, string>;
      try {
        params = opts.share ? { share: opts.share } : { ticket: await wsTicket() };
      } catch { if (!closed) setTimeout(open, Math.min(30000, 2000 * 2 ** retry.current++)); return; }
      if (closed) return;
      ws = new WebSocket(wsUrl(`/ws/trips/${tripId}`, params));
      ws.onopen = () => { setConnected(true); retry.current = 0; };
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type === "position") setPos(msg);
        else if (msg.type === "event") setEvents((e) => [...e, msg]);
        else if (msg.type === "status") setPos((p: any) => ({ ...(p ?? {}), status: msg.status }));
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) setTimeout(open, Math.min(30000, 2000 * 2 ** retry.current++));
      };
    };
    open();
    return () => { closed = true; ws?.close(); };
  }, [tripId, opts.share]);

  return { pos, events, connected };
}

/** Role-scoped live feed (fleet / mandi / all mandis), for map pages. */
export function useLiveFeed(onMessage: (msg: any) => void) {
  const cb = useRef(onMessage);
  cb.current = onMessage;
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    const open = async () => {
      if (!getSession()?.access_token) return;
      let ticket: string;
      try { ticket = await wsTicket(); } catch { if (!closed) setTimeout(open, 10000); return; }
      if (closed) return;
      ws = new WebSocket(wsUrl("/ws/live", { ticket }));
      ws.onmessage = (m) => cb.current(JSON.parse(m.data));
      ws.onclose = () => { if (!closed) setTimeout(open, 5000); };
    };
    open();
    return () => { closed = true; ws?.close(); };
  }, []);
}
