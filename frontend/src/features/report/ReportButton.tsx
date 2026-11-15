// Download the PDF report (doc 10 §Report): POST /sessions/{id}/reports, wait for the report.render job, open the
// presigned PDF. Used in the Results step and the "Ask & report" step.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, newIdempotencyKey } from "../../lib/api";

export default function ReportButton({ sessionId, className = "btn-primary" }: { sessionId: string; className?: string }) {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "working" | "failed">("idle");

  async function download() {
    setState("working");
    try {
      const { report_id } = await api<{ report_id: string }>(`/api/v1/sessions/${sessionId}/reports`,
        { method: "POST", idempotencyKey: newIdempotencyKey() });
      for (let i = 0; i < 60; i++) {  // the server renders it in the background (report.render job)
        const r = await api<{ status: string; url: string | null }>(`/api/v1/reports/${report_id}`);
        if (r.status === "ready" && r.url) { window.open(r.url, "_blank", "noopener"); setState("idle"); return; }
        await new Promise((res) => setTimeout(res, 1000));
      }
      setState("failed");
    } catch { setState("failed"); }
  }

  return (
    <>
      <button className={className} disabled={state === "working"} onClick={download}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          aria-hidden="true"><path d="M12 4v11" /><path d="M7 10l5 5 5-5" /><path d="M5 20h14" /></svg>
        {state === "working" ? t("report_working") : t("report_download")}</button>
      {state === "failed" && <p role="alert" className="alert-error w-full">{t("report_failed")}</p>}
    </>
  );
}
