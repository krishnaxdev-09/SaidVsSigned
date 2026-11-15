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
const TONE: Record<Status, string> = { CONFLICT: "border-rose-200 bg-rose-50 text-rose-900",
  UNCLEAR: "border-amber-200 bg-amber-50 text-amber-900", MATCH: "border-emerald-200 bg-emerald-50 text-emerald-900",
  NOT_VERIFIED: "border-slate-200 bg-slate-50 text-slate-700" };
const BAR: Record<Status, string> = { CONFLICT: "bg-rose-500", UNCLEAR: "bg-amber-500", MATCH: "bg-emerald-500",
  NOT_VERIFIED: "bg-slate-400" };
const SEV: Record<string, string> = { high: "badge-red", medium: "badge-amber", low: "badge-slate" };

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
    <section className="space-y-3" aria-labelledby="results-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="results-title" className="font-semibold">{t("results_title")}</h2>
        <div className="flex flex-wrap gap-2 [&>button]:whitespace-nowrap">
          <button className="btn-secondary" onClick={async () => {
            await api(`/api/v1/sessions/${sessionId}/reverify`, { method: "POST" });
          }}>{t("reverify")}</button>
          <button className="btn-primary"
            disabled={report === "working"} onClick={downloadReport}>
            {report === "working" ? t("report_working") : t("report_download")}</button>
        </div>
      </div>
      {report === "failed" && <p role="alert" className="alert-error">{t("report_failed")}</p>}
      <ul className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4" aria-label="summary">
        {ORDER.map((s) => (
          <li key={s} data-testid={`count-${s}`} className={`rounded-xl border px-3 py-2.5 ${TONE[s]}`}>
            <span className="block text-xs font-medium opacity-80">{t(`status_${s}`)}:</span>
            <strong className="text-2xl font-semibold leading-tight">{results.filter((r) => r.status === s).length}</strong></li>))}
      </ul>
      {results.length === 0 && <p className="muted">{t("no_results")}</p>}
      <ul className="space-y-2">
        {results.map((r) => {
          const spoken = r.evidence.find((e): e is SpokenEvidence => e.kind === "spoken");
          const claim = byClaim.get(r.claim_id);
          // HC-3: offered only when the customer's confirmation is the one thing missing for this result.
          const canConfirm = r.reason_code === "low_asr_confidence" && spoken && !claim?.quote_confirmed_by_user
            && (r.engine_verdicts ?? []).some((v) => v.missing.includes("quote_confirmation"));
          return (
            <li key={r.id} data-testid="result"
              className="relative space-y-1.5 overflow-hidden rounded-xl border border-slate-200 bg-white p-4 pl-5 shadow-sm">
              <span aria-hidden="true" className={`absolute inset-y-0 left-0 w-1.5 ${BAR[r.status]}`} />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong className="text-[15px]">{r.headline}</strong>
                {r.severity !== "info" && <span className={SEV[r.severity] ?? "badge-slate"}>{t(`severity_${r.severity}`)}</span>}
              </div>
              <p className="text-sm">{r.reason}</p>
              {r.changed_because.previous_status && (
                <p className="muted text-xs">{t("updated_because", { trigger: t(`trigger_${r.changed_because.trigger}`),
                  previous: t(`status_${r.changed_because.previous_status}`) })}</p>)}
              {r.suggested_question && <p className="text-sm"><strong>{t("ask_this")}:</strong> {r.suggested_question}</p>}
              {canConfirm && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span>{t("confirm_quote_q")}</span>
                  <button className="btn-play" aria-label="play quote"
                    onClick={() => playSnippet(sessionId, spoken.start_ms - 500, spoken.end_ms + 500)}>▶</button>
                  <button className="btn-secondary" onClick={() => confirmQuote(r)}>{t("confirm_quote_yes")}</button>
                </div>)}
              {claim?.quote_confirmed_by_user && <p className="text-xs">{t("quote_confirmed")}</p>}
              <button className="btn-ghost -ml-2 text-brand-700" onClick={() => setOpen(r)}>{t("evidence")}<span aria-hidden="true">→</span></button>
            </li>);
        })}
      </ul>
      {open && <EvidenceDrawer sessionId={sessionId} result={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
