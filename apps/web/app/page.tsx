"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";

import { Logo, LogoMark } from "@/components/Logo";
import { useServerReady } from "@/components/ServerWake";
import { ROLE_HOME } from "@/lib/api";
import { useSession } from "@/lib/session";

/** Public landing page. Signed-in users go straight to their own screen. */
export default function Home() {
  const { user, ready } = useSession();
  const router = useRouter();
  useServerReady(); // wake the (free, sleeping) API early so sign-in is quick
  useEffect(() => {
    if (ready && user) router.replace(ROLE_HOME[user.role]);
  }, [ready, user, router]);
  // the landing renders straight away (and server-side); only a signed-in visitor is sent on to their screen
  if (ready && user) return <div className="p-8 text-sm text-muted">Loading…</div>;
  return <Landing />;
}

function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!("IntersectionObserver" in window)) { setShown(true); return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setShown(true); io.disconnect(); } },
      { threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return { ref, shown };
}

function Reveal({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  const { ref, shown } = useReveal<HTMLDivElement>();
  return (
    <div ref={ref} style={{ transitionDelay: `${delay}ms` }} className={`reveal ${shown ? "reveal-in" : ""} ${className}`}>
      {children}
    </div>
  );
}

const STEPS = [
  { n: "01", title: "Collect", body: "Daily mandi prices from Agmarknet, weather from Open-Meteo and NASA POWER, Sentinel-2 crop imagery, and GPS from trucks on active trips." },
  { n: "02", title: "Forecast", body: "Tomato prices 1–4 weeks ahead as a p10–p50–p90 range with a spike probability, checked walk-forward against a simple baseline." },
  { n: "03", title: "Track", body: "Lots move farm → truck → mandi with QR checkpoints and live ETA. Tracking runs only during a trip, with the driver's consent." },
  { n: "04", title: "Decide", body: "An optimizer weighs price, transport, spoilage and mandi capacity to suggest where to sell, and which loads to share." },
];

const ROLES: { title: string; does: string[] }[] = [
  { title: "Farmer", does: ["Register a lot and pin the farm", "See the best mandi by net value, with a range", "Follow the truck live, get delivery confirmation"] },
  { title: "FPO / aggregator", does: ["Group members' lots into shipments", "Plan shared truckloads with estimated savings", "Book a fleet and follow every trip"] },
  { title: "Driver", does: ["Accept trips on a phone app", "Share location only during the trip, with consent", "Scan QR codes at pickup and delivery"] },
  { title: "Fleet owner", does: ["Assign trucks and drivers to bookings", "See the fleet on a live map", "Fill empty return legs with nearby loads"] },
  { title: "Mandi trader", does: ["See supply heading to the mandi before it arrives", "Scan deliveries, weigh and price lots", "Compare today's arrivals with a normal day"] },
  { title: "Bulk buyer", does: ["Watch price ranges at chosen mandis", "See spike risk before buying", "Plan procurement 1–4 weeks out"] },
  { title: "Policy analyst", does: ["District price-risk map for Karnataka", "Scenario simulator: rain failure, export ban", "Per-module data status: real or sample"] },
  { title: "Lender / insurer", does: ["Verified shipment history per farmer", "Pickup, trip and delivery evidence in one chain", "Reliability shared only with the farmer's consent"] },
  { title: "Admin / data ops", does: ["Data freshness and real-data readiness", "Model evaluation on shared folds", "Confirm mandi locations, manage sessions"] },
];

function Landing() {
  return (
    <div className="landing overflow-x-hidden">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-page/85 backdrop-blur">
        <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <Link href="/" aria-label="AgriPulse home"><Logo size={32} /></Link>
          <div className="ml-auto hidden items-center gap-6 text-sm text-ink2 md:flex">
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#roles" className="hover:text-ink">Who it&apos;s for</a>
            <a href="#honest" className="hover:text-ink">Honest by design</a>
          </div>
          <div className="ml-auto flex items-center gap-2 md:ml-0">
            <Link href="/login" className="whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium text-ink2 hover:text-ink">Sign in</Link>
            <Link href="/register" className="whitespace-nowrap rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink transition hover:opacity-90">
              Create account
            </Link>
          </div>
        </nav>
      </header>

      {/* hero */}
      <section className="relative">
        <div aria-hidden className="hero-glow pointer-events-none absolute inset-x-0 -top-40 h-[520px]" />
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 pb-16 pt-14 md:grid-cols-[1.05fr_1fr] md:pb-24 md:pt-20">
          <div>
            <p className="fade-up mb-4 inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink2">
              <span className="live-dot h-2 w-2 rounded-full bg-brand" /> Tomato · Karnataka and neighbouring states
            </p>
            <h1 className="fade-up text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl" style={{ animationDelay: "80ms" }}>
              See tomato supply moving <span className="text-brand">before the price moves.</span>
            </h1>
            <p className="fade-up mt-5 max-w-xl text-lg leading-relaxed text-ink2" style={{ animationDelay: "160ms" }}>
              AgriPulse forecasts mandi prices 1–4 weeks ahead as honest ranges, tracks produce live from farm to mandi,
              and turns both into better decisions for farmers, traders and policymakers.
            </p>
            <div className="fade-up mt-8 flex flex-wrap gap-3" style={{ animationDelay: "240ms" }}>
              <Link href="/login" className="rounded-xl bg-brand px-5 py-3 font-medium text-brand-ink shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                Try the live demo
              </Link>
              <a href="#how" className="rounded-xl border border-line bg-surface px-5 py-3 font-medium transition hover:-translate-y-0.5 hover:bg-page">
                How it works
              </a>
            </div>
            <p className="fade-up mt-4 text-xs text-muted" style={{ animationDelay: "320ms" }}>
              The public demo runs on sample prices and simulated trucks.
            </p>
          </div>
          <HeroIllustration />
        </div>
      </section>

      {/* facts */}
      <section className="border-y border-line bg-surface">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-4 py-8 md:grid-cols-4">
          {[["1–4 weeks", "price outlook as p10 / p50 / p90"], ["9 roles", "one chain from farm to mandi"],
            ["18 mandis", "in the Karnataka pilot"], ["Every number", "labelled real or sample"]].map(([k, v], i) => (
            <Reveal key={k} delay={i * 80}>
              <p className="text-2xl font-semibold tracking-tight">{k}</p>
              <p className="mt-1 text-sm text-ink2">{v}</p>
            </Reveal>
          ))}
        </div>
      </section>

      {/* how it works */}
      <section id="how" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20">
        <Reveal>
          <p className="text-sm font-semibold uppercase tracking-wider text-brand">How it works</p>
          <h2 className="mt-2 max-w-2xl text-3xl font-semibold tracking-tight">From raw signals to a decision you can act on</h2>
        </Reveal>
        <div className="relative mt-12 grid gap-6 md:grid-cols-4">
          <div aria-hidden className="flow-line absolute left-0 right-0 top-6 hidden h-px md:block" />
          {STEPS.map((s, i) => (
            <Reveal key={s.n} delay={i * 120} className="relative">
              <div className="relative z-10 mb-5 grid h-12 w-12 place-items-center rounded-full border border-line bg-surface text-sm font-semibold text-brand shadow-sm">
                {s.n}
              </div>
              <h3 className="text-lg font-semibold">{s.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink2">{s.body}</p>
            </Reveal>
          ))}
        </div>
      </section>

      {/* roles */}
      <section id="roles" className="scroll-mt-20 border-t border-line bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-20">
          <Reveal>
            <p className="text-sm font-semibold uppercase tracking-wider text-brand">Who it&apos;s for</p>
            <h2 className="mt-2 max-w-2xl text-3xl font-semibold tracking-tight">Nine roles, each with its own screen</h2>
            <p className="mt-3 max-w-2xl text-ink2">
              Everyone works on the same verified chain, and sees only their own organisation&apos;s data.
            </p>
          </Reveal>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {ROLES.map((r, i) => (
              <Reveal key={r.title} delay={(i % 3) * 90}>
                <div className="role-card h-full rounded-2xl border border-line bg-page p-5">
                  <h3 className="font-semibold">{r.title}</h3>
                  <ul className="mt-3 space-y-2 text-sm text-ink2">
                    {r.does.map((d) => (
                      <li key={d} className="flex gap-2">
                        <svg aria-hidden viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 flex-none text-brand">
                          <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        <span>{d}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* honesty */}
      <section id="honest" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20">
        <div className="grid gap-10 md:grid-cols-[1fr_1.1fr] md:items-center">
          <Reveal>
            <p className="text-sm font-semibold uppercase tracking-wider text-brand">Honest by design</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">It tells you what it doesn&apos;t know</h2>
            <p className="mt-4 leading-relaxed text-ink2">
              Real mandi price history only started collecting in September 2026. Until there is enough of it, every
              forecast, chart and table says where its numbers come from, and results that didn&apos;t hold up are
              reported, not hidden.
            </p>
          </Reveal>
          <div className="grid gap-3">
            {[["SAMPLE DATA", "bg-page text-ink2", "Generated prices used to demonstrate the method, not live mandi rates."],
              ["REAL — LIMITED HISTORY", "bg-warn/15 text-ink", "Real data, but not yet enough for the model to be trusted."],
              ["REAL", "bg-good/10 text-good", "Real data past the readiness threshold, checked per mandi."],
              ["COUNTERFACTUAL ESTIMATE", "bg-brand/10 text-brand", "Scenario answers from stated, sourced assumptions — not a prediction."]].map(([b, cls, text], i) => (
              <Reveal key={b} delay={i * 90}>
                <div className="flex flex-col items-start gap-2 rounded-xl border border-line bg-surface p-4 sm:flex-row sm:gap-4">
                  <span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold tracking-wide ${cls}`}>{b}</span>
                  <p className="text-sm text-ink2">{text}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* cta */}
      <section className="px-4 pb-20">
        <Reveal>
          <div className="cta mx-auto flex max-w-6xl flex-col items-start gap-6 overflow-hidden rounded-3xl bg-brand px-8 py-12 text-brand-ink md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">Walk through a lot&apos;s journey yourself</h2>
              <p className="mt-2 opacity-85">Sign in with any demo role (password agripulse-demo) and follow a load from farm to mandi.</p>
            </div>
            <div className="flex gap-3">
              <Link href="/login" className="whitespace-nowrap rounded-xl bg-brand-ink px-5 py-3 font-medium text-brand transition hover:-translate-y-0.5">Open the demo</Link>
              <Link href="/register" className="whitespace-nowrap rounded-xl border border-brand-ink/40 px-5 py-3 font-medium transition hover:-translate-y-0.5">Create account</Link>
            </div>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-8 text-sm text-muted md:flex-row md:items-center md:justify-between">
          <span className="inline-flex items-center gap-2"><LogoMark size={22} /> AgriPulse · food-inflation early warning</span>
          <span>Data: Agmarknet (data.gov.in), Open-Meteo, NASA POWER, Sentinel-2, OpenStreetMap</span>
        </div>
      </footer>
    </div>
  );
}

/** Illustration only: a route with a moving truck and a forecast range. Labelled as an illustration, not data. */
function HeroIllustration() {
  const route = "M 44 214 C 110 200, 120 120, 190 118 S 280 70, 332 58";
  return (
    <div className="fade-up relative mb-28" style={{ animationDelay: "200ms" }}>
      <div className="relative rounded-3xl border border-line bg-surface p-5 shadow-[0_20px_60px_-30px_rgba(0,0,0,0.35)]">
        <div className="mb-3 flex items-center justify-between text-xs text-muted">
          <span className="font-medium text-ink2">Kolar belt → Kolar APMC</span>
          <span className="rounded-full border border-line px-2 py-0.5">Illustration</span>
        </div>
        <svg viewBox="0 0 380 260" className="w-full" role="img" aria-label="Illustration: a truck moving along a route from a farm to a mandi">
          <defs>
            <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" className="stroke-line" strokeWidth="1" />
            </pattern>
          </defs>
          <rect width="380" height="260" rx="14" fill="url(#grid)" opacity="0.7" />
          <path d={route} fill="none" className="stroke-line" strokeWidth="8" strokeLinecap="round" />
          <path d={route} fill="none" className="route-dash stroke-brand" strokeWidth="3" strokeLinecap="round" strokeDasharray="7 9" />
          {/* farm */}
          <circle cx="44" cy="214" r="7" className="fill-surface stroke-ink2" strokeWidth="2" />
          <text x="44" y="240" textAnchor="middle" className="fill-ink2 text-[11px]">Farm</text>
          {/* mandi */}
          <circle cx="332" cy="58" r="16" className="pulse-ring fill-brand/20" />
          <circle cx="332" cy="58" r="8" className="fill-brand" />
          <text x="332" y="92" textAnchor="middle" className="fill-ink2 text-[11px]">Mandi</text>
          {/* truck */}
          <g className="truck">
            <circle r="9" className="fill-warn" />
            <circle r="3.5" className="fill-surface" />
            <animateMotion dur="7s" repeatCount="indefinite" path={route} keyPoints="0;1" keyTimes="0;1" calcMode="linear" />
          </g>
        </svg>
        <div className="mt-2 grid grid-cols-3 gap-2 text-center text-xs">
          <div className="rounded-lg bg-page p-2"><p className="text-muted">ETA</p><p className="font-semibold">4:10 PM</p></div>
          <div className="rounded-lg bg-page p-2"><p className="text-muted">Load</p><p className="font-semibold">2 t</p></div>
          <div className="rounded-lg bg-page p-2"><p className="text-muted">Tracking</p><p className="font-semibold text-brand">On, consented</p></div>
        </div>
      </div>

      <div className="float-card absolute -bottom-32 -left-3 w-60 rounded-2xl border border-line bg-surface p-4 shadow-[0_18px_40px_-24px_rgba(0,0,0,0.4)] sm:-left-8">
        <p className="text-xs text-muted">Price range, next 4 weeks</p>
        <svg viewBox="0 0 200 70" className="mt-2 w-full" aria-hidden>
          <path d="M0 40 C 40 36, 70 30, 100 26 S 160 18, 200 12 L 200 44 C 160 48, 130 50, 100 50 S 40 50, 0 46 Z" className="band fill-brand/15" />
          <path d="M0 43 C 40 42, 70 40, 100 38 S 160 32, 200 28" fill="none" className="draw stroke-brand" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <div className="mt-1 flex justify-between text-[11px] text-muted"><span>p10</span><span className="font-medium text-ink2">p50</span><span>p90</span></div>
      </div>
    </div>
  );
}
