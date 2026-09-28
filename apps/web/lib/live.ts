"use client";

import { useEffect, useRef } from "react";
import { getToken, WS_URL } from "./api";

/**
 * Subscribe to a server WebSocket and call `onMessage` for each JSON frame.
 * Reconnects with backoff; the path is e.g. `/ws/live` or `/ws/trips/12`.
 * `query` is appended as-is (`share=...`); the JWT is added unless `anonymous` is set.
 */
export function useLive(path: string | null, onMessage: (msg: Record<string, unknown>) => void, opts: { query?: string; anonymous?: boolean } = {}) {
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    if (!path) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let delay = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      const params = new URLSearchParams(opts.query || "");
      if (!opts.anonymous) {
        const t = getToken();
        if (!t) return;
        params.set("token", t);
      }
      ws = new WebSocket(`${WS_URL}${path}?${params.toString()}`);
      ws.onopen = () => (delay = 1000);
      ws.onmessage = (e) => {
        try {
          handler.current(JSON.parse(e.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (e) => {
        // 4403/4401 = not allowed, 4410 = share link expired: don't hammer the server.
        if (closed || [4401, 4403, 4410].includes(e.code)) return;
        timer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [path, opts.query, opts.anonymous]);
}

/** Re-run `fn` every `ms` while the tab is visible. Used as a fallback next to live sockets. */
export function usePoll(fn: () => void, ms: number, deps: unknown[] = []) {
  const f = useRef(fn);
  f.current = fn;
  useEffect(() => {
    f.current();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") f.current();
    }, ms);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}
