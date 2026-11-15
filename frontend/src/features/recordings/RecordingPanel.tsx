// T1.1: upload or replace the session's call recording and show its processing status (doc 02 §4).
// Status refreshes when `recording.status` events arrive (the session page invalidates this query on every event).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, newIdempotencyKey, upload } from "../../lib/api";
import type { Recording } from "../../lib/types";
import LiveRecorder from "./LiveRecorder";

export default function RecordingPanel({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["recording", sessionId],
    queryFn: () => api<Recording | null>(`/api/v1/sessions/${sessionId}/recording`) });
  const [file, setFile] = useState<File | null>(null);
  const [split, setSplit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"upload" | "live">("upload");
  const key = useRef<string | null>(null); // one Idempotency-Key per chosen file, reused on retry

  const rec = q.data ?? null;
  const replaceable = rec && rec.stage !== "failed";

  async function submit() {
    if (!file) return;
    setBusy(true); setError(null);
    key.current ??= newIdempotencyKey();
    const form = new FormData();
    form.append("file", file);
    form.append("channel_layout", split ? "stereo_split" : "mono");
    try {
      if (replaceable) await upload(`/api/v1/recordings/${rec.recording_id}`, "PUT", form, key.current);
      else await upload(`/api/v1/sessions/${sessionId}/recordings`, "POST", form, key.current);
      setFile(null); key.current = null;
      await qc.invalidateQueries({ queryKey: ["recording", sessionId] });
      await qc.invalidateQueries({ queryKey: ["state", sessionId] });
    } catch (e) {
      setError(e instanceof ApiError ? `${t("upload_failed")}: ${e.message} (${e.code})` : t("upload_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="rec-title">
      <h2 id="rec-title" className="font-semibold">{t("recording_title")}</h2>
      {rec && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm" data-testid="recording">
          <dt>{t("stage")}</dt>
          <dd data-testid="recording-stage">
            {rec.status === "normalised" ? t("awaiting_transcription") : rec.stage}
            {rec.status_code ? ` (${rec.status_code})` : ""}
          </dd>
          {rec.duration_ms !== null && (<><dt>{t("duration")}</dt><dd>{(rec.duration_ms / 1000).toFixed(1)} s</dd></>)}
          {rec.warnings.length > 0 && (<><dt>{t("warnings")}</dt><dd>{rec.warnings.join(", ")}</dd></>)}
        </dl>
      )}
      {rec?.stage === "failed" && <p className="alert-warn">{t("recording_failed")}</p>}
      {!replaceable && (
        <div role="radiogroup" aria-label={t("recording_mode")} className="flex flex-wrap gap-2 text-sm">
          {(["upload", "live"] as const).map((m) => (
            <label key={m} className={`choice ${mode === m ? "choice-on" : ""}`}>
              <input type="radio" name="rec-mode" checked={mode === m} onChange={() => setMode(m)} />
              {t(m === "upload" ? "mode_upload" : "mode_live")}
            </label>))}
        </div>)}
      {mode === "live" && !replaceable ? (
        <LiveRecorder sessionId={sessionId} onFinished={async () => {
          await qc.invalidateQueries({ queryKey: ["recording", sessionId] });
          await qc.invalidateQueries({ queryKey: ["state", sessionId] });
        }} />
      ) : (<>
      <label className="block text-sm">
        {t("choose_file")}
        <input type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.webm,.ogg" className="mt-1.5 block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); key.current = null; }} />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> {t("stereo_split")}
      </label>
      <button className="btn-primary" disabled={!file || busy}
        onClick={submit}>{busy ? t("uploading") : replaceable ? t("replace") : t("upload")}</button>
      {error && <p role="alert" className="alert-error">{error}</p>}
      </>)}
    </section>
  );
}
