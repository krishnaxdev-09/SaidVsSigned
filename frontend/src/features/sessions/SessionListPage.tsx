import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import type { Session, SessionState } from "../../lib/types";

export const STATE_BADGE: Record<SessionState, string> = {
  consent_pending: "badge-amber", ready: "badge-brand", processing: "badge-brand", completed: "badge-green",
  failed: "badge-red", deleted: "badge-slate" };

export default function SessionListPage() {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => api<{ items: Session[] }>("/api/v1/sessions") });
  const items = q.data?.items ?? [];
  const plus = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
      aria-hidden="true"><path d="M12 5v14" /><path d="M5 12h14" /></svg>);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 pb-5">
        <div>
          <h1>{t("sessions")}</h1>
          <p className="muted mt-0.5">{t("sessions_sub")}</p>
        </div>
        <Link className="btn-primary" to="/sessions/new">{plus}{t("new_session")}</Link>
      </div>
      {q.isSuccess && items.length === 0 && (
        <div className="flex flex-col items-center rounded-lg border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
          <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-md border border-slate-200 bg-slate-50 text-slate-500">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
              strokeLinejoin="round"><path d="M4 8h9" /><path d="M4 16h16" /><path d="M17 5l3 3-3 3" /></svg></span>
          <h2 className="mt-4">{t("no_sessions")}</h2>
          <p className="muted mt-1 max-w-sm">{t("no_sessions_help")}</p>
          <Link className="btn-primary mt-5" to="/sessions/new">{plus}{t("new_session")}</Link>
        </div>
      )}
      {items.length > 0 && (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-card">
          {items.map((s) => (
            <li key={s.id}>
              <Link to={`/sessions/${s.id}`} className="group flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50 sm:px-5">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{s.lender_name ?? t(`product_${s.product_type ?? "other_loan"}`)}</p>
                  <p className="text-xs text-slate-500">
                    {t(`product_${s.product_type ?? "other_loan"}`)} · {new Date(s.created_at).toLocaleDateString(i18n.language)}</p>
                </div>
                <span className={STATE_BADGE[s.state]}>{t(`session_state_${s.state}`)}</span>
                <span aria-hidden="true" className="text-slate-400 transition group-hover:translate-x-0.5 group-hover:text-slate-600">→</span>
              </Link>
            </li>
          ))}
        </ul>)}
    </div>
  );
}
