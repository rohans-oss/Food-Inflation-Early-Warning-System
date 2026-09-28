"use client";

import { useEffect, useRef, useState } from "react";
import { Btn, inputCls } from "./ui";

interface Detector {
  detect(src: HTMLVideoElement): Promise<{ rawValue: string }[]>;
}

/**
 * QR scan with the camera where the browser has BarcodeDetector (Chrome on Android), with a typed fallback
 * everywhere else. Calls onCode once with the decoded token.
 */
export default function Scanner({ onCode, busy }: { onCode: (code: string) => void; busy?: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const [on, setOn] = useState(false);
  const [typed, setTyped] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!on) return;
    let stream: MediaStream | null = null;
    let stop = false;
    (async () => {
      const BD = (window as unknown as { BarcodeDetector?: new (o: { formats: string[] }) => Detector }).BarcodeDetector;
      if (!BD) {
        setMsg("This browser can't scan QR codes. Type the code under the QR instead.");
        setOn(false);
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      } catch {
        setMsg("Camera permission denied.");
        setOn(false);
        return;
      }
      if (!video.current) return;
      video.current.srcObject = stream;
      await video.current.play();
      const det = new BD({ formats: ["qr_code"] });
      while (!stop) {
        try {
          const codes = await det.detect(video.current);
          if (codes.length) {
            onCode(codes[0].rawValue.trim());
            break;
          }
        } catch {
          /* frame not ready */
        }
        await new Promise((r) => setTimeout(r, 300));
      }
      setOn(false);
    })();
    return () => {
      stop = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [on, onCode]);

  return (
    <div className="space-y-2">
      {on ? (
        <div className="space-y-1">
          <video ref={video} className="w-full max-w-xs rounded border" muted playsInline />
          <Btn variant="secondary" onClick={() => setOn(false)}>Stop camera</Btn>
        </div>
      ) : (
        <Btn variant="secondary" onClick={() => { setMsg(null); setOn(true); }} disabled={busy}>Scan QR with camera</Btn>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (typed.trim()) onCode(typed.trim());
        }}
      >
        <input className={inputCls} placeholder="…or type the code" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <Btn type="submit" disabled={busy || !typed.trim()}>Confirm</Btn>
      </form>
      {msg && <p className="text-xs text-slate-600">{msg}</p>}
    </div>
  );
}
