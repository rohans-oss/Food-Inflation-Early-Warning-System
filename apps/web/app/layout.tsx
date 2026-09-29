import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";

import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = {
  title: "AgriPulse",
  description: "Tomato price early warning and farm-to-mandi tracking",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
