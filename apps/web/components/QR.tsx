"use client";

import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";

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

/** Camera QR scan on any browser with a camera: the built-in BarcodeDetector where it exists (Android Chrome), else
 * jsQR on video frames (laptops, iPhone Safari, Firefox). Manual entry always works. `onCode` is read through a ref,
 * so a page that re-renders (live updates, polling) does not restart the camera. */
export function QRScanner({ onCode, label = "Scan", autoStart = false }: { onCode: (code: string) => void; label?: string; autoStart?: boolean }) {
  const [on, setOn] = useState(autoStart);
  const [manual, setManual] = useState("");
  const [msg, setMsg] = useState("");
  const cb = useRef(onCode);
  cb.current = onCode;
  const videoRef = useRef<HTMLVideoElement>(null);
  const hasCamera = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  useEffect(() => {
    if (!on) return;
    let stream: MediaStream | null = null;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    setMsg("");
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
        const video = videoRef.current;
        if (!video || stop) return;
        video.srcObject = stream;
        await video.play();
        const native = "BarcodeDetector" in window ? new (window as any).BarcodeDetector({ formats: ["qr_code"] }) : null;
        const jsQR = native ? null : (await import("jsqr")).default;
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const tick = async () => {
          if (stop) return;
          let text: string | null = null;
          if (video.readyState >= 2 && video.videoWidth) {
            if (native) {
              const codes = await native.detect(video).catch(() => []);
              text = codes[0]?.rawValue ?? null;
            } else if (jsQR && ctx) {
              const w = Math.min(640, video.videoWidth);
              const h = Math.round((video.videoHeight / video.videoWidth) * w);
              canvas.width = w; canvas.height = h;
              ctx.drawImage(video, 0, 0, w, h);
              const img = ctx.getImageData(0, 0, w, h);
              text = jsQR(img.data, w, h, { inversionAttempts: "dontInvert" })?.data ?? null;
            }
          }
          if (text && !stop) {
            stop = true;
            try { navigator.vibrate?.(120); } catch { /* not supported */ }
            setOn(false);
            cb.current(text.trim());
            return;
          }
          timer = setTimeout(tick, 150);
        };
        tick();
      } catch (e: any) {
        setMsg(e?.name === "NotAllowedError" ? "Camera permission was refused. Allow the camera for this site, or type the code below."
          : e?.name === "NotFoundError" ? "No camera found on this device. Type the code shown under the driver's QR instead."
          : (e?.message ?? "Camera unavailable"));
        setOn(false);
      }
    })();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [on]);

  return (
    <div className="space-y-2">
      {on && (
        <div className="relative w-full max-w-sm">
          <video ref={videoRef} playsInline muted className="w-full rounded-lg bg-black" />
          <div className="pointer-events-none absolute inset-8 rounded-lg border-2 border-white/80" />
          <p className="mt-1 text-xs text-muted">Point the camera at the QR on the driver&apos;s phone. Turn up the phone&apos;s brightness if it doesn&apos;t read.</p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {hasCamera && (
          <button type="button" onClick={() => setOn((v) => !v)} className="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-ink">
            {on ? "Stop camera" : `${label} with camera`}
          </button>
        )}
        <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="…or type the code under the QR"
          className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm" />
        <button type="button" disabled={!manual.trim()} onClick={() => { cb.current(manual.trim()); setManual(""); }}
          className="rounded-lg border border-line px-3 py-2 text-sm disabled:opacity-50">Submit</button>
      </div>
      {msg && <p className="text-xs text-critical">{msg}</p>}
    </div>
  );
}
