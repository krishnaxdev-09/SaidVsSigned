// Session page as an ordered pipeline (doc 10 §2): each step's panel — and so its API calls — mounts only when the
// step before it is done, in the order the backend works: consent → recording → speakers → claims → documents →
// results → ask & report. Progress queries are gated with `enabled` in the same order; live events refresh them.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import ConsentForm from "../consent/ConsentForm";
import RecordingPanel from "../recordings/RecordingPanel";
import SpeakerConfirm from "../speakers/SpeakerConfirm";
import ClaimList from "../claims/ClaimList";
import TranscriptView from "../transcript/TranscriptView";
import DocumentUpload from "../documents/DocumentUpload";
import ResultsDashboard from "../results/ResultsDashboard";
import AskPanel from "../ask/AskPanel";
import { api } from "../../lib/api";
import { connectEvents } from "../../lib/events";
import { applyEvent } from "../../lib/eventLog";
import type { Claim, ConsentStatus, EventEnvelope, LoanDocument, Recording, Session, Speakers,
  VerificationResult } from "../../lib/types";
import { currentStep, pipelineSteps, unlocked, type Step, type StepId } from "./pipeline";

const ICON: Record<Step["state"], string> = { done: "✓", working: "…", todo: "●", failed: "!", locked: "🔒" };

export default function SessionPage() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["state", id],
    queryFn: () => api<{ session: Session; consent: ConsentStatus; last_event_seq: number }>(`/api/v1/sessions/${id}/state`) });
  const consent = q.data?.consent;

  // Progress queries, gated in pipeline order (the panels below share these cache keys).
  const recording = useQuery({ queryKey: ["recording", id], enabled: !!consent?.active,
    queryFn: () => api<Recording | null>(`/api/v1/sessions/${id}/recording`) });
  const transcribed = recording.data?.stage === "ready";
  const speakers = useQuery({ queryKey: ["speakers", id], enabled: transcribed, retry: false,
    queryFn: () => api<Speakers>(`/api/v1/sessions/${id}/speakers`) });
  const confirmed = !!speakers.data?.confirmed;
  const claims = useQuery({ queryKey: ["claims", id], enabled: confirmed, retry: false,
    queryFn: () => api<Claim[]>(`/api/v1/sessions/${id}/claims?state=all`) });
  const documents = useQuery({ queryKey: ["documents", id], enabled: confirmed && claims.data !== undefined,
    queryFn: () => api<LoanDocument[]>(`/api/v1/sessions/${id}/documents`) });
  const docReady = (documents.data ?? []).some((d) => d.status === "ready");
  const results = useQuery({ queryKey: ["results", id], enabled: docReady,
    queryFn: () => api<VerificationResult[]>(`/api/v1/sessions/${id}/results`) });

  const steps = pipelineSteps({ consent, recording: recording.data, speakers: speakers.data, claims: claims.data,
    documents: documents.data, results: results.data });
  const current = currentStep(steps);
  const [open, setOpen] = useState<StepId | null>(null);
  const shown = open ?? current;

  const log = useRef({ lastSeq: 0, events: [] as EventEnvelope[] });
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  useEffect(() => connectEvents(id, () => log.current.lastSeq, (ev) => {
    log.current = applyEvent(log.current, ev);
    setEvents(log.current.events);
    qc.invalidateQueries({ queryKey: ["state", id] });
    if (ev.type === "recording.status") qc.invalidateQueries({ queryKey: ["recording", id] });
    if (ev.type === "transcript.ready" || ev.type === "speaker.updated") qc.invalidateQueries({ queryKey: ["speakers", id] });
    if (ev.type === "transcript.ready") qc.invalidateQueries({ queryKey: ["transcript", id] });
    if (ev.type.startsWith("claim.") || ev.type === "speaker.updated") qc.invalidateQueries({ queryKey: ["claims", id] });
    if (ev.type === "document.status") qc.invalidateQueries({ queryKey: ["documents", id] });
    if (ev.type === "verification.updated") qc.invalidateQueries({ queryKey: ["results", id] });
    if (ev.type === "qa.answer") qc.invalidateQueries({ queryKey: ["qa", id] });
  }), [id, qc]);

  // When a step finishes, move the open panel on to the next step.
  const prev = useRef<StepId>(current);
  useEffect(() => { if (prev.current !== current) { prev.current = current; setOpen(null); } }, [current]);

  if (!q.data) return null;
  const { session } = q.data;

  const panel = (sid: StepId, body: ReactNode) => {
    const s = steps.find((x) => x.id === sid)!;
    const n = steps.indexOf(s) + 1;
    const isOpen = shown === sid && s.state !== "locked";
    return (
      <section key={sid} data-testid={`step-${sid}`} data-state={s.state}
        className={`rounded border ${isOpen ? "border-slate-400" : "border-slate-200"}`}>
        <button className="flex w-full items-center justify-between gap-2 p-3 text-left disabled:cursor-not-allowed"
          disabled={s.state === "locked"} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : sid)}>
          <span className="font-semibold">{n}. {t(`step_${sid}`)}</span>
          <span className="text-xs text-slate-600">
            {ICON[s.state]} {t(`step_state_${s.state}`)}{s.detail ? ` · ${s.detail}` : ""}</span>
        </button>
        {s.state === "locked" && <p className="px-3 pb-3 text-xs text-slate-500">{t("step_locked", { n: n - 1 })}</p>}
        {isOpen && <div className="space-y-3 px-3 pb-3">{body}</div>}
      </section>
    );
  };

  return (
    <div className="space-y-3">
      <p className="text-sm">{t("state")}: <strong data-testid="state">{session.state}</strong>
        {session.state_reason ? ` (${session.state_reason})` : ""}</p>
      <ol aria-label={t("pipeline")} className="flex flex-wrap gap-2 text-xs">
        {steps.map((s, i) => (
          <li key={s.id} className={`rounded-full border px-2 py-1 ${s.id === current ? "border-slate-900 font-semibold" : ""}
            ${s.state === "locked" ? "opacity-50" : ""}`}>{ICON[s.state]} {i + 1}. {t(`step_${s.id}`)}</li>))}
      </ol>
      {panel("consent", consent?.active
        ? <p className="text-sm">{consent.attestation_note}</p>
        : <ConsentForm sessionId={id} />)}
      {panel("recording", unlocked(steps, "recording") ? <RecordingPanel sessionId={id} /> : null)}
      {panel("speakers", unlocked(steps, "speakers") ? <SpeakerConfirm sessionId={id} /> : null)}
      {panel("claims", unlocked(steps, "claims") ? <><ClaimList sessionId={id} /><TranscriptView sessionId={id} /></> : null)}
      {panel("documents", unlocked(steps, "documents") ? <DocumentUpload sessionId={id} /> : null)}
      {panel("results", unlocked(steps, "results") ? <ResultsDashboard sessionId={id} /> : null)}
      {panel("ask", unlocked(steps, "ask") ? <AskPanel sessionId={id} /> : null)}
      {consent?.active && (
        <button className="rounded border border-red-700 px-3 py-2 text-red-700"
          onClick={() => api(`/api/v1/sessions/${id}/consents/customer`, { method: "DELETE" })}>{t("revoke")}</button>
      )}
      <details className="text-xs text-slate-500"><summary>{t("activity")}</summary>
        <ul aria-label="events">{events.map((e) => <li key={e.seq}>#{e.seq} {e.type}</li>)}</ul></details>
    </div>
  );
}
