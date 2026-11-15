// Live microphone capture — "record live, verify on stop" (user request 2026-11-15). The browser records with
// MediaRecorder and streams a chunk every few seconds (PUT /live/{id}/chunks/{n}, retried); Stop sends the last chunk
// and POST /live/{id}/finish, after which the recording follows exactly the same pipeline as an uploaded file.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, newIdempotencyKey, putBlob } from "../../lib/api";

const TIMESLICE_MS = 3000;
const MIME = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];

export function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return MIME.find((m) => MediaRecorder.isTypeSupported(m));
}

async function withRetry<T>(fn: () => Promise<T>, tries = 3): Promise<T> {
  for (let i = 1; ; i++) {
    try { return await fn(); } catch (e) {
      if (i >= tries || (e instanceof ApiError && e.status < 500 && e.status !== 429)) throw e;
      await new Promise((r) => setTimeout(r, 500 * 2 ** i));
    }
  }
}

type Phase = "idle" | "starting" | "recording" | "finishing" | "error";

export default function LiveRecorder({ sessionId, onFinished }: { sessionId: string; onFinished: () => void }) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [sent, setSent] = useState(0);
  const r = useRef<{ rec?: MediaRecorder; stream?: MediaStream; live?: string; seq: number; uploads: Promise<unknown>[];
    ctx?: AudioContext; timer?: number; raf?: number; started: number }>({ seq: 0, uploads: [], started: 0 });

  const cleanup = () => {
    const s = r.current;
    s.stream?.getTracks().forEach((tr) => tr.stop());
    void s.ctx?.close().catch(() => {});
    if (s.timer) window.clearInterval(s.timer);
    if (s.raf) cancelAnimationFrame(s.raf);
  };
  useEffect(() => () => cleanup(), []);

  async function start() {
    setError(null);
    const mime = pickMime();
    if (!mime || !navigator.mediaDevices?.getUserMedia) { setError(t("live_unsupported")); setPhase("error"); return; }
    setPhase("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const { live_id } = await api<{ live_id: string }>(`/api/v1/sessions/${sessionId}/live`,
        { method: "POST", idempotencyKey: newIdempotencyKey() });
      const rec = new MediaRecorder(stream, { mimeType: mime });
      const s = r.current;
      Object.assign(s, { rec, stream, live: live_id, seq: 0, uploads: [], started: Date.now() });
      rec.ondataavailable = (ev) => {
        if (!ev.data.size) return;
        const n = s.seq++;
        s.uploads.push(withRetry(() => putBlob(`/api/v1/live/${live_id}/chunks/${n}`, ev.data)).then(() => setSent((x) => x + 1)));
      };
      rec.start(TIMESLICE_MS);
      s.timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - s.started) / 1000)), 500);
      try {  // input level meter (best effort)
        const ctx = new AudioContext();
        const an = ctx.createAnalyser();
        ctx.createMediaStreamSource(stream).connect(an);
        const buf = new Uint8Array(an.fftSize);
        const tick = () => {
          an.getByteTimeDomainData(buf);
          let sum = 0;
          for (const v of buf) sum += ((v - 128) / 128) ** 2;
          setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3));
          s.raf = requestAnimationFrame(tick);
        };
        s.ctx = ctx;
        tick();
      } catch { /* no meter */ }
      setPhase("recording");
    } catch (e) {
      cleanup();
      setError(e instanceof ApiError ? e.message : t("live_mic_denied"));
      setPhase("error");
    }
  }

  async function stop() {
    const s = r.current;
    if (!s.rec || !s.live) return;
    setPhase("finishing");
    await new Promise<void>((res) => { s.rec!.onstop = () => res(); s.rec!.stop(); });
    cleanup();
    try {
      await Promise.all(s.uploads);
      await withRetry(() => api(`/api/v1/live/${s.live}/finish`, { method: "POST", body: { chunks: s.seq } }));
      setPhase("idle");
      onFinished();
    } catch (e) {
      setError(e instanceof ApiError ? `${t("live_failed")}: ${e.message}` : t("live_failed"));
      setPhase("error");
    }
  }

  async function cancel() {
    const s = r.current;
    s.rec?.stop();
    cleanup();
    if (s.live) await api(`/api/v1/live/${s.live}`, { method: "DELETE" }).catch(() => {});
    setPhase("idle"); setElapsed(0); setSent(0);
  }

  const mm = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  return (
    <div className="space-y-2" data-testid="live-recorder">
      <p className="alert-warn text-xs">{t("live_notice")}</p>
      {phase === "recording" || phase === "finishing" ? (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-sm" aria-live="polite">
            <span className="inline-block h-3 w-3 animate-pulse rounded-full bg-mismatch-fg" aria-hidden="true" />
            {t("live_recording")} <strong data-testid="live-timer">{mm}</strong> · {t("live_sent", { n: sent })}
          </p>
          <div className="h-2 w-full rounded bg-slate-200" aria-hidden="true">
            <div className="h-2 rounded bg-match-fg" style={{ width: `${Math.round(level * 100)}%` }} />
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={phase === "finishing"}
              onClick={stop}>{phase === "finishing" ? t("live_finishing") : t("live_stop")}</button>
            <button className="btn-secondary" disabled={phase === "finishing"} onClick={cancel}>{t("live_cancel")}</button>
          </div>
        </div>
      ) : (
        <button className="btn-record" disabled={phase === "starting"}
          onClick={start}>{phase === "starting" ? t("live_starting") : t("live_start")}</button>
      )}
      {error && <p role="alert" className="alert-error">{error}</p>}
    </div>
  );
}
