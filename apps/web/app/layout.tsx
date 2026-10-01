import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";

import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = {
  title: "AgriPulse",
  description: "Vegetable price early warning and farm-to-mandi tracking",
};
// The site-wide banner was removed at the owner's request (2026-10-01); sample data keeps its "Sample" badges.

export const viewport: Viewport = { width: "device-width", initialScale: 1, colorScheme: "light" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
