// Session page as an ordered pipeline (doc 10 §2): each step's panel — and so its API calls — mounts only when the
// step before it is done, in the order the backend works: consent → recording → speakers → claims → documents →
// results → ask & report. Progress queries are gated with `enabled` in the same order; live events refresh them.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import ConsentForm from "../consent/ConsentForm";
import RecordingPanel from "../recordings/RecordingPanel";
import SpeakerConfirm from "../speakers/SpeakerConfirm";
import ClaimList from "../claims/ClaimList";
import TranscriptView from "../transcript/TranscriptView";
import DocumentUpload from "../documents/DocumentUpload";
import ResultsDashboard from "../results/ResultsDashboard";
import AskPanel from "../ask/AskPanel";
import ReportButton from "../report/ReportButton";
import { api } from "../../lib/api";
import { connectEvents } from "../../lib/events";
import { applyEvent } from "../../lib/eventLog";
import type { Claim, ConsentStatus, EventEnvelope, LoanDocument, Recording, Session, Speakers,
  VerificationResult } from "../../lib/types";
import { currentStep, pipelineSteps, unlocked, type Step, type StepId } from "./pipeline";
import { STATE_BADGE as SESSION_BADGE } from "./SessionListPage";

const DOT: Record<Step["state"], string> = {
  done: "bg-match-fg text-white", working: "bg-brand-600 text-white animate-pulse", todo: "bg-brand-600 text-white",
  failed: "bg-mismatch-fg text-white", locked: "border border-slate-300 bg-white text-slate-500" };
const Check = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"
    aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7" /></svg>);
const STATE_BADGE: Record<Step["state"], string> = { done: "badge-green", working: "badge-brand", todo: "badge-brand",
  failed: "badge-red", locked: "badge-slate" };

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

  const doneCount = steps.filter((s) => s.state === "done").length;
  const dot = (s: Step, n: number, size: string) => (
    <span aria-hidden="true" className={`grid ${size} shrink-0 place-items-center rounded-full text-xs font-semibold ${DOT[s.state]}`}>
      {s.state === "done" ? <Check /> : s.state === "failed" ? "!" : n}</span>);

  const panel = (sid: StepId, body: ReactNode) => {
    const s = steps.find((x) => x.id === sid)!;
    const n = steps.indexOf(s) + 1;
    const isOpen = shown === sid && s.state !== "locked";
    return (
      <section key={sid} id={`step-${sid}`} data-testid={`step-${sid}`} data-state={s.state}
        className={`scroll-mt-4 ${s.state === "locked" ? "bg-slate-50/60" : "bg-white"}`}>
        <button className="flex min-h-[52px] w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:hover:bg-transparent sm:px-5"
          disabled={s.state === "locked"} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : sid)}>
          {dot(s, n, "h-6 w-6")}
          <span className="min-w-0 flex-1">
            {isOpen && <span className="block text-xs font-medium text-brand-600">{t("step_n_of", { n, total: steps.length })}</span>}
            <span className={`block font-medium ${isOpen ? "text-base font-semibold" : s.state === "locked" ? "text-slate-500" : ""}`}>
              {t(`step_${sid}`)}</span>
            {s.state === "locked" && <span className="block text-xs text-slate-500">{t("step_locked", { n: n - 1 })}</span>}
          </span>
          {s.state !== "locked" && <span className={STATE_BADGE[s.state]}>{t(`step_state_${s.state}`)}{s.detail ? ` · ${s.detail}` : ""}</span>}
          {s.state !== "locked" && <span aria-hidden="true" className={`text-slate-400 transition ${isOpen ? "rotate-180" : ""}`}>⌄</span>}
        </button>
        {isOpen && <div className="space-y-4 border-t border-slate-100 bg-white px-4 pb-5 pt-4 sm:px-5">{body}</div>}
      </section>
    );
  };

  return (
    <div className="flex flex-wrap items-start gap-6 lg:gap-8">
      <aside className="min-w-0 flex-[1_1_240px] space-y-5 lg:sticky lg:top-6 lg:max-w-[260px]">
        <div className="space-y-1">
          <Link to="/sessions" className="text-sm text-slate-500 hover:text-slate-900">← {t("sessions")}</Link>
          <h1 className="break-words">{session.lender_name ?? t(`product_${session.product_type ?? "other_loan"}`)}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] text-slate-500">{t(`product_${session.product_type ?? "other_loan"}`)}</span>
            <span className={SESSION_BADGE[session.state]}>{t(`session_state_${session.state}`)}
              <span className="sr-only">{t("state")}: </span><span data-testid="state" className="sr-only">{session.state}</span></span>
          </div>
        </div>
        {session.state_reason && <p className="alert-warn">{session.state_reason}</p>}
        <nav aria-label={t("pipeline")} className="space-y-3">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium uppercase tracking-[0.06em] text-slate-500">{t("pipeline")}</span>
            <span className="text-slate-500">{doneCount}/{steps.length}</span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-slate-200" aria-hidden="true">
            <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${(doneCount / steps.length) * 100}%` }} />
          </div>
          <ol className="flex flex-wrap gap-1 lg:flex-col lg:gap-0">
            {steps.map((s, i) => {
              const isShown = s.id === shown && s.state !== "locked";
              return (
                <li key={s.id}>
                  <button disabled={s.state === "locked"} onClick={() => { setOpen(s.id);
                    document.getElementById(`step-${s.id}`)?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }}
                    className={`flex min-h-[44px] w-full items-center gap-2.5 rounded-md px-2 text-left text-[13px] transition lg:min-h-[34px]
                      disabled:cursor-not-allowed ${isShown ? "bg-brand-50 font-semibold text-brand-600"
                      : s.state === "done" ? "text-slate-900 hover:bg-slate-100" : "text-slate-500 hover:bg-slate-100 disabled:hover:bg-transparent"}`}>
                    {dot(s, i + 1, "h-5 w-5 text-[11px]")}
                    <span className="hidden lg:inline">{t(`step_${s.id}`)}</span>
                    <span className="sr-only lg:hidden">{t(`step_${s.id}`)}</span>
                  </button>
                </li>);
            })}
          </ol>
        </nav>
      </aside>

      <div className="min-w-0 flex-[999_1_560px] space-y-4">
      <div className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-card">
      {panel("consent", consent?.active
        ? <p className="text-sm">{consent.attestation_note}</p>
        : <ConsentForm sessionId={id} />)}
      {panel("recording", unlocked(steps, "recording") ? <RecordingPanel sessionId={id} /> : null)}
      {panel("speakers", unlocked(steps, "speakers") ? <SpeakerConfirm sessionId={id} /> : null)}
      {panel("claims", unlocked(steps, "claims") ? <><ClaimList sessionId={id} /><TranscriptView sessionId={id} /></> : null)}
      {panel("documents", unlocked(steps, "documents") ? <DocumentUpload sessionId={id} /> : null)}
      {panel("results", unlocked(steps, "results") ? <ResultsDashboard sessionId={id} /> : null)}
      {panel("ask", unlocked(steps, "ask") ? <>
        <div className="card-tight flex flex-wrap items-center justify-between gap-3 p-4">
          <div><p className="font-medium">{t("report_title")}</p><p className="muted">{t("report_help")}</p></div>
          <ReportButton sessionId={id} />
        </div>
        <AskPanel sessionId={id} />
      </> : null)}
      </div>
      {consent?.active && (
        <button className="btn-danger"
          onClick={() => api(`/api/v1/sessions/${id}/consents/customer`, { method: "DELETE" })}>{t("revoke")}</button>
      )}
      <details className="text-xs text-slate-500"><summary className="cursor-pointer py-2">{t("activity")}</summary>
        <ul aria-label="events">{events.map((e) => <li key={e.seq}>#{e.seq} {e.type}</li>)}</ul></details>
      </div>
    </div>
  );
}
