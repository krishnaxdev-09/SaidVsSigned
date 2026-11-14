// T2.7 evidence drawer (doc 10, doc 08 §3.3): the agent's verbatim quote with snippet playback, the document page
// image with the clause bboxes highlighted (normalised 0–1, top-left origin → percentage overlay), the clause text,
// the clarification question and the result history.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { mmss, playSnippet } from "../../lib/snippet";
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
    <figure className="space-y-1">
      <figcaption className="text-xs text-slate-600">{t("page_n", { n: ev.page })}</figcaption>
      <div className="relative w-full overflow-hidden rounded border" data-testid="page-image">
        {q.data && <img src={q.data.url} alt={t("page_n", { n: ev.page })} className="block w-full" />}
        {ev.bboxes.map((b, i) => (
          <div key={i} data-testid="bbox" className="pointer-events-none absolute rounded-sm bg-yellow-300/40 ring-2 ring-amber-500"
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
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="drawer-title"
      className="fixed inset-0 z-20 flex justify-end bg-black/30" onClick={onClose}>
      <aside className="h-full w-full max-w-xl space-y-4 overflow-y-auto bg-white p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-2">
          <h3 id="drawer-title" className="font-semibold">{result.headline}</h3>
          <button className="h-11 min-w-11 rounded border px-3" onClick={onClose}>{t("close")}</button>
        </div>
        <p className="text-sm">{result.reason}</p>
        {spoken && (
          <section className="space-y-1">
            <h4 className="text-sm font-semibold">{t("what_agent_said")}</h4>
            <p className="flex gap-2 text-sm">
              <button aria-label={`play ${mmss(spoken.start_ms)}`} className="h-11 min-w-11 rounded border px-2"
                onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}>▶</button>
              <span>“{spoken.quote}” <span className="text-slate-500">({mmss(spoken.start_ms)})</span>
                {spoken.role_assignment === "auto" && <span className="ml-1 text-xs">({t("auto_detected")})</span>}</span>
            </p>
          </section>)}
        {docs.map((d) => (
          <section key={d.clause_id} className="space-y-2">
            <h4 className="text-sm font-semibold">{t("what_document_says")}</h4>
            <blockquote className="whitespace-pre-wrap border-l-4 border-amber-400 pl-3 text-sm" data-testid="clause-text">
              {d.clause_text}</blockquote>
            <PageImage ev={d} />
          </section>))}
        {calcs.map((c) => (
          <section key={c.calculation_id} className="space-y-1">
            <h4 className="text-sm font-semibold">{t("calculation")}</h4>
            <p className="text-sm" data-testid="calc-summary">{c.summary}</p>
          </section>))}
        {result.suggested_question && (
          <p className="rounded bg-slate-100 p-2 text-sm"><strong>{t("ask_this")}:</strong> {result.suggested_question}</p>)}
        {(hist.data?.length ?? 0) > 1 && (
          <section>
            <h4 className="text-sm font-semibold">{t("history")}</h4>
            <ol className="text-xs">{hist.data!.map((h) => (
              <li key={h.id}>v{h.version}: {t(`status_${h.status}`)}</li>))}</ol>
          </section>)}
      </aside>
    </div>
  );
}
