// T2.7 results dashboard v1 (doc 10, doc 08 §2, §8–§10): the four approved statuses with counts, templated
// headline/reason, severity, "Updated because …", the clarification question, HC-3 play-and-confirm for numeric
// quotes, re-check, and the evidence drawer. Updated live on `verification.updated` events.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, newIdempotencyKey } from "../../lib/api";
import { playSnippet } from "../../lib/snippet";
import type { Claim, SpokenEvidence, Status, VerificationResult } from "../../lib/types";
import EvidenceDrawer from "./EvidenceDrawer";

const ORDER: Status[] = ["CONFLICT", "UNCLEAR", "MATCH", "NOT_VERIFIED"];
const TONE: Record<Status, string> = { CONFLICT: "border-red-600 bg-red-50", UNCLEAR: "border-amber-500 bg-amber-50",
  MATCH: "border-green-600 bg-green-50", NOT_VERIFIED: "border-slate-400 bg-slate-50" };

export default function ResultsDashboard({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState<VerificationResult | null>(null);
  const [report, setReport] = useState<"idle" | "working" | "failed">("idle");

  async function downloadReport() {
    setReport("working");
    try {
      const { report_id } = await api<{ report_id: string }>(`/api/v1/sessions/${sessionId}/reports`,
        { method: "POST", idempotencyKey: newIdempotencyKey() });
      for (let i = 0; i < 60; i++) {  // the server renders it in the background (report.render job)
        const r = await api<{ status: string; url: string | null }>(`/api/v1/reports/${report_id}`);
        if (r.status === "ready" && r.url) { window.open(r.url, "_blank", "noopener"); setReport("idle"); return; }
        await new Promise((res) => setTimeout(res, 1000));
      }
      setReport("failed");
    } catch { setReport("failed"); }
  }
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

  return (
    <section className="space-y-3 rounded border border-slate-200 p-4" aria-labelledby="results-title">
      <div className="flex items-center justify-between">
        <h2 id="results-title" className="font-semibold">{t("results_title")}</h2>
        <div className="flex gap-2">
          <button className="h-11 rounded border px-3 text-sm" onClick={async () => {
            await api(`/api/v1/sessions/${sessionId}/reverify`, { method: "POST" });
          }}>{t("reverify")}</button>
          <button className="h-11 rounded bg-slate-900 px-3 text-sm text-white disabled:opacity-50"
            disabled={report === "working"} onClick={downloadReport}>
            {report === "working" ? t("report_working") : t("report_download")}</button>
        </div>
      </div>
      {report === "failed" && <p role="alert" className="text-sm text-red-700">{t("report_failed")}</p>}
      <ul className="flex flex-wrap gap-2 text-sm" aria-label="summary">
        {ORDER.map((s) => (
          <li key={s} data-testid={`count-${s}`} className={`rounded border px-2 py-1 ${TONE[s]}`}>
            {t(`status_${s}`)}: <strong>{results.filter((r) => r.status === s).length}</strong></li>))}
      </ul>
      {results.length === 0 && <p className="text-sm text-slate-600">{t("no_results")}</p>}
      <ul className="space-y-2">
        {results.map((r) => {
          const spoken = r.evidence.find((e): e is SpokenEvidence => e.kind === "spoken");
          const claim = byClaim.get(r.claim_id);
          // HC-3: offered only when the customer's confirmation is the one thing missing for this result.
          const canConfirm = r.reason_code === "low_asr_confidence" && spoken && !claim?.quote_confirmed_by_user
            && (r.engine_verdicts ?? []).some((v) => v.missing.includes("quote_confirmation"));
          return (
            <li key={r.id} data-testid="result" className={`space-y-1 rounded border-l-4 p-3 ${TONE[r.status]}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong>{r.headline}</strong>
                {r.severity !== "info" && <span className="text-xs">{t(`severity_${r.severity}`)}</span>}
              </div>
              <p className="text-sm">{r.reason}</p>
              {r.changed_because.previous_status && (
                <p className="text-xs text-slate-600">{t("updated_because", { trigger: t(`trigger_${r.changed_because.trigger}`),
                  previous: t(`status_${r.changed_because.previous_status}`) })}</p>)}
              {r.suggested_question && <p className="text-sm"><strong>{t("ask_this")}:</strong> {r.suggested_question}</p>}
              {canConfirm && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span>{t("confirm_quote_q")}</span>
                  <button className="h-11 min-w-11 rounded border px-2" aria-label="play quote"
                    onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}>▶</button>
                  <button className="h-11 rounded border px-3" onClick={() => confirmQuote(r)}>{t("confirm_quote_yes")}</button>
                </div>)}
              {claim?.quote_confirmed_by_user && <p className="text-xs">{t("quote_confirmed")}</p>}
              <button className="text-sm underline" onClick={() => setOpen(r)}>{t("evidence")}</button>
            </li>);
        })}
      </ul>
      {open && <EvidenceDrawer sessionId={sessionId} result={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
