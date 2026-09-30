"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { LogoMark } from "@/components/Logo";
import { QR } from "@/components/QR";
import { api } from "@/lib/api";
import { inr, num } from "@/lib/format";

/** Proof of delivery & sale. Public by its unguessable link, so the farmer can show or send it to the mandi /
 * commission agent; anyone holding it can check it against AgriPulse. Print / "Save as PDF" gives a paper copy. */
export default function Receipt() {
  const { token } = useParams<{ token: string }>();
  const [r, setR] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api(`/public/receipts/${token}`, { auth: false }).then(setR).catch((e) => setErr(e.message));
  }, [token]);

  if (err) return <main className="mx-auto max-w-2xl p-8"><p className="text-critical">{err}</p></main>;
  if (!r) return <main className="mx-auto max-w-2xl p-8 text-sm text-muted">Loading receipt…</main>;
  const paid = r.payment?.status === "paid";

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 print:max-w-none print:p-0">
      <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink2">Show this to the mandi or commission agent, or send them the link.</p>
        <div className="flex gap-2">
          <button onClick={() => navigator.clipboard?.writeText(r.verify_url)} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm">Copy link</button>
          <button onClick={() => window.print()} className="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-ink">Print / Save as PDF</button>
        </div>
      </div>

      <article className="relative overflow-hidden rounded-2xl border border-line bg-white p-8 text-[#111] print:rounded-none print:border-0">
        {r.simulated && (
          <div aria-hidden className="pointer-events-none absolute inset-0 grid place-items-center">
            <span className="-rotate-12 text-7xl font-black tracking-widest text-critical/10">SIMULATED</span>
          </div>
        )}
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
          <div className="flex items-center gap-3">
            <LogoMark size={40} />
            <div>
              <p className="text-lg font-semibold">AgriPulse</p>
              <p className="text-sm text-[#555]">Proof of delivery &amp; sale</p>
            </div>
          </div>
          <div className="text-right text-sm">
            <p className="font-mono text-base font-semibold">{r.receipt_no}</p>
            <p className="text-[#555]">Issued {r.issued_local}</p>
            {r.simulated && <p className="mt-1 inline-block rounded bg-critical/10 px-2 py-0.5 text-xs font-semibold text-critical">SIMULATED DEMO TRIP · not a real sale</p>}
          </div>
        </header>

        <section className="grid gap-4 border-b border-line py-5 text-sm sm:grid-cols-2">
          <Party label="Farmer (seller)" value={r.farmer} sub={r.pickup_label || undefined} />
          <Party label="Mandi" value={r.mandi} sub={r.mandi_district} />
          <Party label="Buyer / weighed by" value={r.buyer ?? "–"} />
          <Party label="Transport" value={r.transporter ?? "–"} sub={[r.vehicle, r.driver].filter(Boolean).join(" · ") || undefined} />
        </section>

        <section className="border-b border-line py-5">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs uppercase tracking-wide text-[#666]">
              <th className="pb-2">Item</th><th className="pb-2">Declared</th><th className="pb-2">Weighed</th><th className="pb-2">Rate</th><th className="pb-2 text-right">Amount</th>
            </tr></thead>
            <tbody><tr className="border-t border-line">
              <td className="py-2">{r.crop} · grade {r.grade} · lot #{r.lot_id}</td>
              <td className="py-2">{num(r.declared_tons, 2)} t</td>
              <td className="py-2">{num(r.weight_kg, 0)} kg</td>
              <td className="py-2">{inr(r.price_per_quintal)} / quintal</td>
              <td className="py-2 text-right text-base font-semibold">{inr(r.amount)}</td>
            </tr></tbody>
          </table>
          <p className="mt-3 text-sm">
            Payment: {paid
              ? <b className="text-good">Paid · {String(r.payment.method).toUpperCase()}{r.payment.reference ? ` · ref ${r.payment.reference}` : ""} · {r.payment.paid_local}</b>
              : <b className="text-critical">Due from the buyer</b>}
            {r.payment?.received_local && <span className="text-[#555]"> · farmer confirmed receipt {r.payment.received_local}</span>}
          </p>
        </section>

        <section className="border-b border-line py-5">
          <h2 className="mb-3 text-sm font-semibold">Delivery evidence</h2>
          <ol className="space-y-2 text-sm">
            {r.timeline.map((s: any) => (
              <li key={s.step} className="grid grid-cols-[1.5rem_1fr_auto] items-start gap-2">
                <span className={s.verified ? "text-good" : "text-[#999]"}>{s.verified ? "✓" : "–"}</span>
                <span><b>{s.step}</b> <span className="text-[#555]">· {s.evidence}</span></span>
                <span className="text-right text-[#555]">{s.at_local ?? "not recorded"}</span>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-xs text-[#555]">
            Route {r.distance_km != null ? `${num(r.distance_km, 0)} km` : "–"}{r.route_source && r.route_source !== "osrm" ? " (approx.)" : ""} ·
            {" "}{r.gps_points} GPS positions recorded during the trip, with the driver&apos;s consent.
          </p>
        </section>

        <footer className="flex flex-wrap items-center justify-between gap-4 pt-5">
          <p className="max-w-md text-xs text-[#555]">
            Check this receipt: scan the code or open the link. It shows the same record held by AgriPulse. QR scans and GPS
            are the evidence; AgriPulse records the payment but does not transfer money.
          </p>
          <QR value={r.verify_url} size={132} />
        </footer>
      </article>
    </main>
  );
}

function Party({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-[#666]">{label}</p>
      <p className="font-semibold">{value}</p>
      {sub && <p className="text-[#555]">{sub}</p>}
    </div>
  );
}
