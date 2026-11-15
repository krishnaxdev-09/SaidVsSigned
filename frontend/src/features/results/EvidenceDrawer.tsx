// T2.7 evidence drawer (doc 10, doc 08 §3.3): the agent's verbatim quote with snippet playback, the document page
// image with the clause bboxes highlighted (normalised 0–1, top-left origin → percentage overlay), the clause text,
// the clarification question and the result history.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { mmss, playSnippet } from "../../lib/snippet";
import { PlayIcon, StatusChip } from "./status";
import type { BBox, CalculationEvidence, DocumentEvidence, SpokenEvidence, VerificationResult } from "../../lib/types";

export function bboxStyle(b: BBox): React.CSSProperties {
  return { left: `${b.x0 * 100}%`, top: `${b.y0 * 100}%`, width: `${(b.x1 - b.x0) * 100}%`,
           height: `${(b.y1 - b.y0) * 100}%` };
}

function PageImage({ ev }: { ev: DocumentEvidence }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["page-image", ev.document_id, ev.page], staleTime: 240_000,
    queryFn: () => api<{ url: string }>(`/api/v1/documents/${ev.document_id}/pages/${ev.page}/image`) });
  return (
    <figure className="space-y-1.5">
      <figcaption className="mono">{t("page_n", { n: ev.page })}</figcaption>
      <div className="relative w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card" data-testid="page-image">
        {q.data && <img src={q.data.url} alt={t("page_n", { n: ev.page })} className="block w-full" />}
        {ev.bboxes.map((b, i) => (
          <div key={i} data-testid="bbox" className="pointer-events-none absolute rounded-sm bg-[#FFC43D]/35 ring-2 ring-[#D99A00]"
            style={bboxStyle(b)} />))}
      </div>
    </figure>
  );
}

export default function EvidenceDrawer({ sessionId, result, onClose }:
  { sessionId: string; result: VerificationResult; onClose: () => void }) {
  const { t } = useTranslation();
  const spoken = result.evidence.find((e): e is SpokenEvidence => e.kind === "spoken");
  const docs = result.evidence.filter((e): e is DocumentEvidence => e.kind === "document");
  // Only present after HC-5 (the server never sends finance evidence while finance is hidden).
  const calcs = result.evidence.filter((e): e is CalculationEvidence => e.kind === "calculation");
  const hist = useQuery({ queryKey: ["history", result.id],
    queryFn: () => api<VerificationResult[]>(`/api/v1/results/${result.id}/history`) });
  const [copied, setCopied] = useState(false);
  async function copyQuestion() {
    try { await navigator.clipboard.writeText(result.suggested_question ?? ""); setCopied(true); } catch { /* clipboard blocked */ }
  }
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="drawer-title"
      className="fixed inset-0 z-20 flex justify-end bg-ink/45 backdrop-blur-[2px]" onClick={onClose}>
      <aside className="h-full w-full max-w-xl space-y-6 overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-2xl sm:p-6"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-2">
            <StatusChip status={result.status} extra={result.severity !== "info" ? t(`severity_${result.severity}`) : undefined} />
            <h3 id="drawer-title" className="text-lg font-semibold leading-7">{result.headline}</h3>
          </div>
          <button className="btn-secondary shrink-0" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              aria-hidden="true"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>{t("close")}</button>
        </div>
        <p className="text-slate-600">{result.reason}</p>
        {spoken && (
          <section className="space-y-2.5">
            <h4 className="eyebrow">{t("what_agent_said")}</h4>
            <div className="space-y-3 rounded-lg border border-slate-200 p-4">
              <div className="flex items-start gap-3">
                <button aria-label={`play ${mmss(spoken.start_ms)}`}
                  className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition hover:bg-brand-700"
                  onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}><PlayIcon size={18} /></button>
                <p className="text-[15px] leading-6">“{spoken.quote}”</p>
              </div>
              <p className="mono">{t("agent")} · {mmss(spoken.start_ms)}
                {spoken.role_assignment === "auto" && <span className="ml-1">({t("auto_detected")})</span>}</p>
            </div>
          </section>)}
        {docs.map((d) => (
          <section key={d.clause_id} className="space-y-2.5">
            <h4 className="eyebrow">{t("what_document_says")}</h4>
            <blockquote className="whitespace-pre-wrap rounded-lg border border-clarify-line bg-[#FFF8E6] px-4 py-3 leading-6" data-testid="clause-text">
              {d.clause_text}</blockquote>
            <PageImage ev={d} />
          </section>))}
        {calcs.map((c) => (
          <section key={c.calculation_id} className="space-y-2">
            <h4 className="eyebrow">{t("calculation")}</h4>
            <p className="rounded-xl bg-slate-50 px-4 py-3 font-mono text-[13px]" data-testid="calc-summary">{c.summary}</p>
          </section>))}
        {result.suggested_question && (
          <section className="space-y-2.5 rounded-lg border border-slate-200 bg-slate-50 p-4">
            <h4 className="text-sm font-semibold">{t("ask_this_before")}</h4>
            <p>{result.suggested_question}</p>
            <button className="btn-secondary" onClick={copyQuestion}>{copied ? `✓ ${t("copied")}` : t("copy_question")}</button>
          </section>)}
        {(hist.data?.length ?? 0) > 1 && (
          <section className="space-y-2">
            <h4 className="eyebrow">{t("history")}</h4>
            <ol className="space-y-1 text-sm">{hist.data!.map((h) => (
              <li key={h.id}>v{h.version}: {t(`status_${h.status}`)}</li>))}</ol>
          </section>)}
      </aside>
    </div>
  );
}
