"use client";

import { useEffect, useState } from "react";

import { apiBase } from "@/lib/api";

export type ServerState = "checking" | "ready" | "waking" | "down";

/** Pings the API's /health until it answers. Free hosting puts the API to sleep when idle, and the first request can
 * take about a minute; pages show that instead of failing silently. */
export function useServerReady(maxSeconds = 150): ServerState {
  const [state, setState] = useState<ServerState>("checking");
  useEffect(() => {
    let stop = false;
    const started = Date.now();
    const ping = async () => {
      while (!stop) {
        try {
          const ctl = new AbortController();
          const t = setTimeout(() => ctl.abort(), 10000);
          const r = await fetch(`${apiBase()}/health`, { signal: ctl.signal, cache: "no-store" });
          clearTimeout(t);
          if (r.ok) { setState("ready"); return; }
        } catch { /* asleep or starting */ }
        if (stop) return;
        if (Date.now() - started > maxSeconds * 1000) { setState("down"); return; }
        setState("waking");
        await new Promise((res) => setTimeout(res, 3000));
      }
    };
    ping();
    return () => { stop = true; };
  }, [maxSeconds]);
  return state;
}

export function ServerNotice({ state }: { state: ServerState }) {
  if (state === "ready" || state === "checking") return null;
  return (
    <p role="status" className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${state === "down"
      ? "border-critical/40 text-critical" : "border-warn/50 text-ink2"}`}>
      {state === "waking" && <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />}
      {state === "waking"
        ? "Starting the server. The free demo sleeps when idle; this can take up to a minute."
        : "The server is not answering. Please try again in a few minutes."}
    </p>
  );
}
