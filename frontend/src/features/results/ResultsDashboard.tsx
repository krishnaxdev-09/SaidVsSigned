// T2.7 results dashboard v1 (doc 10, doc 08 §2, §8–§10): the four approved statuses with counts, templated
// headline/reason, severity, "Updated because …", the clarification question, HC-3 play-and-confirm for numeric
// quotes, re-check, and the evidence drawer. Updated live on `verification.updated` events.
// Ledger layout: a summary band, then each possible mismatch / clarification as a "said vs signed" card;
// matches and couldn't-check results as a compact list.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { mmss, playSnippet } from "../../lib/snippet";
import type { Claim, DocumentEvidence, SpokenEvidence, VerificationResult } from "../../lib/types";
import EvidenceDrawer from "./EvidenceDrawer";
import ReportButton from "../report/ReportButton";
import { PlayIcon, STATUS_ORDER as ORDER, STATUS_TONE, StatusChip, StatusIcon } from "./status";

const SEV: Record<string, string> = { high: "border-mismatch-line text-mismatch-fg", medium: "border-clarify-line text-clarify-fg",
  low: "border-slate-300 text-slate-600" };

export default function ResultsDashboard({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState<VerificationResult | null>(null);
  const q = useQuery({ queryKey: ["results", sessionId],
    queryFn: () => api<VerificationResult[]>(`/api/v1/sessions/${sessionId}/results`) });
  const claims = useQuery({ queryKey: ["claims", sessionId],
    queryFn: () => api<Claim[]>(`/api/v1/sessions/${sessionId}/claims?state=all`) });
  const byClaim = new Map((claims.data ?? []).map((c) => [c.id, c]));
  const results = [...(q.data ?? [])].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));

  async function confirmQuote(r: VerificationResult) {
    await api(`/api/v1/claims/${r.claim_id}/confirm-quote`, { method: "POST", body: { confirmed: true } });
    await qc.invalidateQueries({ queryKey: ["claims", sessionId] });
  }

  const conflicts = results.filter((r) => r.status === "CONFLICT").length;
  const unclear = results.filter((r) => r.status === "UNCLEAR").length;
  const verdict = conflicts ? t("verdict_conflict", { count: conflicts }) : unclear ? t("verdict_unclear") : t("verdict_clear");
  const detailed = results.filter((r) => r.status === "CONFLICT" || r.status === "UNCLEAR");
  const compact = results.filter((r) => r.status === "MATCH" || r.status === "NOT_VERIFIED");
  const changed = (r: VerificationResult) => r.changed_because.previous_status && (
    <p className="text-xs text-slate-500">{t("updated_because", { trigger: t(`trigger_${r.changed_because.trigger}`),
      previous: t(`status_${r.changed_because.previous_status}`) })}</p>);
  const evidenceBtn = (r: VerificationResult) => (
    <button className="btn-ghost -mr-2 font-semibold text-brand-600 hover:text-brand-700" onClick={() => setOpen(r)}>
      {t("evidence")}<span aria-hidden="true">→</span></button>);

  return (
    <section className="space-y-4" aria-labelledby="results-title">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <h2 id="results-title" className="sr-only">{t("results_title")}</h2>
        <div className="flex flex-wrap gap-2 [&>button]:whitespace-nowrap">
          <button className="btn-secondary" onClick={async () => {
            await api(`/api/v1/sessions/${sessionId}/reverify`, { method: "POST" });
          }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.6" /><path d="M20 4v5h-5" /></svg>
            {t("reverify")}</button>
          <ReportButton sessionId={sessionId} />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-card">
        {results.length > 0 && (
          <div className="flex items-start gap-3 border-b border-slate-200 px-4 py-3.5 sm:px-5">
            <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md ${STATUS_TONE[conflicts ? "CONFLICT" : unclear ? "UNCLEAR" : "MATCH"].tile}`}>
              <StatusIcon status={conflicts ? "CONFLICT" : unclear ? "UNCLEAR" : "MATCH"} /></span>
            <div className="min-w-0">
              <span className="eyebrow block">{t("before_you_sign")}</span>
              <p className="text-[15px] font-medium leading-6">{verdict}</p>
            </div>
          </div>)}
        <ul className="grid grid-cols-2 gap-px bg-slate-200 sm:grid-cols-4" aria-label="summary">
          {ORDER.map((s) => (
            <li key={s} data-testid={`count-${s}`} className="flex flex-col gap-1 bg-white px-4 py-3 sm:px-5">
              <span className={`flex items-center gap-1.5 text-xs font-medium ${STATUS_TONE[s].fg}`}><StatusIcon status={s} size={13} />
                <span className="text-slate-600">{t(`status_${s}`)}</span></span>
              <strong className={`text-2xl font-semibold tabular-nums leading-8 ${STATUS_TONE[s].fg}`}>
                {results.filter((r) => r.status === s).length}</strong>
            </li>))}
        </ul>
      </div>

      {results.length === 0 && <p className="muted">{t("no_results")}</p>}

      <ul className="space-y-4">
        {detailed.map((r) => {
          const spoken = r.evidence.find((e): e is SpokenEvidence => e.kind === "spoken");
          const doc = r.evidence.find((e): e is DocumentEvidence => e.kind === "document");
          const claim = byClaim.get(r.claim_id);
          // HC-3: offered only when the customer's confirmation is the one thing missing for this result.
          const canConfirm = r.reason_code === "low_asr_confidence" && spoken && !claim?.quote_confirmed_by_user
            && (r.engine_verdicts ?? []).some((v) => v.missing.includes("quote_confirmation"));
          return (
            <li key={r.id} data-testid="result" className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-card">
              <div className="space-y-1.5 px-4 pb-3.5 pt-4 sm:px-5">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusChip status={r.status} />
                  {r.severity !== "info" && (
                    <span className={`rounded border px-1.5 py-px text-xs font-medium ${SEV[r.severity] ?? SEV.low}`}>
                      {t(`severity_${r.severity}`)}</span>)}
                </div>
                <strong className="block pt-1 text-[15px] font-semibold leading-6">{r.headline}</strong>
                <p className="text-slate-600">{r.reason}</p>
                {changed(r)}
              </div>
              {(spoken || doc) && (
                <div className={`grid border-t border-slate-100 ${spoken && doc ? "sm:grid-cols-2" : ""}`}>
                  {spoken && (
                    <div className={`space-y-2.5 px-4 py-3.5 sm:px-5 ${doc ? "sm:border-r sm:border-slate-100" : ""}`}>
                      <span className="eyebrow block">{t("what_agent_said")}</span>
                      <div className="flex items-start gap-3">
                        <button className="btn-play" aria-label={`${t("play")} ${mmss(spoken.start_ms)}`}
                          onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}><PlayIcon /></button>
                        <p className="text-[15px] leading-6">“{spoken.quote}”</p>
                      </div>
                      <span className="mono block">{t("agent")} · {mmss(spoken.start_ms)}</span>
                    </div>)}
                  {doc && (
                    <div className={`space-y-2.5 bg-[#FBFAF7] px-4 py-3.5 sm:px-5 ${spoken ? "border-t border-slate-100 sm:border-t-0" : ""}`}>
                      <span className="eyebrow block">{t("what_document_says")}</span>
                      <p className="line-clamp-4 whitespace-pre-line">{doc.clause_text}</p>
                      <span className="mono block">{t("page_n", { n: doc.page })}</span>
                    </div>)}
                </div>)}
              {canConfirm && (
                <div className="mx-4 mb-4 sm:mx-5 flex flex-wrap items-center gap-3 rounded-md bg-slate-50 px-3.5 py-3 text-sm">
                  <span className="min-w-0 flex-[1_1_240px]">{t("confirm_quote_q")}</span>
                  <button className="btn-play" aria-label="play quote"
                    onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}><PlayIcon /></button>
                  <button className="btn-secondary" onClick={() => confirmQuote(r)}>{t("confirm_quote_yes")}</button>
                </div>)}
              {claim?.quote_confirmed_by_user && <p className="px-4 pb-3 sm:px-5 text-xs text-match-fg">✓ {t("quote_confirmed")}</p>}
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-1 sm:px-5">
                {r.suggested_question
                  ? <p className="min-w-0 flex-[1_1_320px] py-2 text-sm text-slate-600"><strong className="text-slate-900">{t("ask_this")}:</strong> {r.suggested_question}</p>
                  : <span />}
                {evidenceBtn(r)}
              </div>
            </li>);
        })}
      </ul>

      {compact.length > 0 && (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-card" aria-labelledby="other-results">
          <h3 id="other-results" className="border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-xs font-semibold uppercase tracking-[0.06em] text-slate-500 sm:px-5">{t("other_results")}</h3>
          <ul className="divide-y divide-slate-100">
            {compact.map((r) => {
              const spoken = r.evidence.find((e): e is SpokenEvidence => e.kind === "spoken");
              return (
                <li key={r.id} data-testid="result" className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 sm:px-5">
                  <span className={STATUS_TONE[r.status].fg} title={t(`status_${r.status}`)}>
                    <StatusIcon status={r.status} size={16} /><span className="sr-only">{t(`status_${r.status}`)}</span></span>
                  <span className="min-w-0 flex-1">
                    <span className="block">{r.headline}</span>
                    {changed(r)}
                  </span>
                  {spoken && <span className="mono">{mmss(spoken.start_ms)}</span>}
                  {evidenceBtn(r)}
                </li>);
            })}
          </ul>
        </section>)}
      {open && <EvidenceDrawer sessionId={sessionId} result={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
