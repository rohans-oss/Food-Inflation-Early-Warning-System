import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";

import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = {
  title: "AgriPulse",
  description: "Tomato price early warning and farm-to-mandi tracking",
};
// Public demo builds set NEXT_PUBLIC_DEMO_NOTICE (docs/deployment.md "Public demo"); a real deployment leaves it empty.
const DEMO_NOTICE = process.env.NEXT_PUBLIC_DEMO_NOTICE ?? "";

export const viewport: Viewport = { width: "device-width", initialScale: 1, colorScheme: "light" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        {DEMO_NOTICE && (
          <div role="note" className="bg-ink px-4 py-1.5 text-center text-xs text-page">{DEMO_NOTICE}</div>
        )}
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
