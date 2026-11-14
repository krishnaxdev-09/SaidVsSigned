// T1.1: upload or replace the session's call recording and show its processing status (doc 02 §4).
// Status refreshes when `recording.status` events arrive (the session page invalidates this query on every event).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, newIdempotencyKey, upload } from "../../lib/api";
import type { Recording } from "../../lib/types";

export default function RecordingPanel({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["recording", sessionId],
    queryFn: () => api<Recording | null>(`/api/v1/sessions/${sessionId}/recording`) });
  const [file, setFile] = useState<File | null>(null);
  const [split, setSplit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    <section className="space-y-3 rounded border border-slate-200 p-4" aria-labelledby="rec-title">
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
      {rec?.stage === "failed" && <p className="text-sm text-amber-800">{t("recording_failed")}</p>}
      <label className="block text-sm">
        {t("choose_file")}
        <input type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.webm,.ogg" className="mt-1 block"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); key.current = null; }} />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> {t("stereo_split")}
      </label>
      <button className="rounded bg-slate-900 px-3 py-2 text-white disabled:opacity-50" disabled={!file || busy}
        onClick={submit}>{busy ? t("uploading") : replaceable ? t("replace") : t("upload")}</button>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  );
}
