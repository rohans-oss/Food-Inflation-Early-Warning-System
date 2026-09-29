"use client";

import QRCode from "qrcode";
import { useEffect, useState } from "react";

export function QR({ value, size = 220, caption }: { value: string; size?: number; caption?: string }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    QRCode.toDataURL(value, { width: size, margin: 1, errorCorrectionLevel: "M" }).then(setSrc).catch(() => setSrc(""));
  }, [value, size]);
  return (
    <figure className="inline-block text-center">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} width={size} height={size} alt={caption ?? "QR code"} className="rounded-lg bg-white p-2" />
      ) : (
        <div style={{ width: size, height: size }} className="rounded-lg bg-page" />
      )}
      <figcaption className="mt-1 max-w-[260px] break-all font-mono text-[11px] text-muted">{value}</figcaption>
    </figure>
  );
}

/** Camera QR scan where the browser supports BarcodeDetector; manual entry everywhere else. */
export function QRScanner({ onCode, label = "Scan" }: { onCode: (code: string) => void; label?: string }) {
  const [on, setOn] = useState(false);
  const [manual, setManual] = useState("");
  const [msg, setMsg] = useState("");
  const supported = typeof window !== "undefined" && "BarcodeDetector" in window;

  useEffect(() => {
    if (!on) return;
    let stream: MediaStream | null = null;
    let stop = false;
    const video = document.getElementById("qr-video") as HTMLVideoElement | null;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (!video) return;
        video.srcObject = stream;
        await video.play();
        const detector = new (window as any).BarcodeDetector({ formats: ["qr_code"] });
        const tick = async () => {
          if (stop) return;
          const codes = await detector.detect(video).catch(() => []);
          if (codes.length) {
            setOn(false);
            onCode(codes[0].rawValue);
            return;
          }
          requestAnimationFrame(tick);
        };
        tick();
      } catch (e: any) {
        setMsg(e.message ?? "Camera unavailable");
        setOn(false);
      }
    })();
    return () => {
      stop = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [on, onCode]);

  return (
    <div className="space-y-2">
      {on && <video id="qr-video" playsInline muted className="w-full max-w-sm rounded-lg" />}
      <div className="flex flex-wrap gap-2">
        {supported && (
          <button type="button" onClick={() => setOn((v) => !v)} className="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-ink">
            {on ? "Stop camera" : `${label} with camera`}
          </button>
        )}
        <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="…or paste the code"
          className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm" />
        <button type="button" disabled={!manual.trim()} onClick={() => onCode(manual.trim())}
          className="rounded-lg border border-line px-3 py-2 text-sm disabled:opacity-50">Submit</button>
      </div>
      {msg && <p className="text-xs text-critical">{msg}</p>}
    </div>
  );
}
