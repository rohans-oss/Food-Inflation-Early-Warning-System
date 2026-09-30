/** AgriPulse mark: a leaf-topped rounded tile carrying a price "pulse" line. */
export function LogoMark({ size = 36, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" className={className} role="img" aria-label="AgriPulse">
      <rect x="1" y="4" width="38" height="35" rx="10" className="fill-brand" />
      <path d="M20 5.5c1.6-3 4.8-4.4 8-3.9-.6 3.2-3.3 5.5-6.6 5.6" className="fill-brand" />
      <path d="M7 24h6l3-8 5 14 4-10 2.5 4H33" fill="none" className="stroke-brand-ink" strokeWidth="2.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ size = 36 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="text-lg font-semibold tracking-tight">AgriPulse</span>
    </span>
  );
}
