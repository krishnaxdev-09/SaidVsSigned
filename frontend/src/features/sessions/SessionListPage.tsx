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
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1>{t("sessions")}</h1>
          <p className="muted mt-1">{t("sessions_sub")}</p>
        </div>
        <Link className="btn-primary" to="/sessions/new">+ {t("new_session")}</Link>
      </div>
      {q.isSuccess && items.length === 0 && (
        <div className="card items-center py-10 text-center">
          <div aria-hidden="true" className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-brand-50 text-xl text-brand-700">🎧</div>
          <h2 className="mt-3">{t("no_sessions")}</h2>
          <p className="muted mx-auto max-w-sm">{t("no_sessions_help")}</p>
          <Link className="btn-primary mx-auto mt-2 w-fit" to="/sessions/new">{t("new_session")}</Link>
        </div>
      )}
      <ul className="grid gap-3">
        {items.map((s) => (
          <li key={s.id}>
            <Link to={`/sessions/${s.id}`}
              className="card-tight flex items-center justify-between gap-3 p-4 shadow-sm transition hover:border-brand-500 hover:shadow-card">
              <div className="min-w-0">
                <p className="truncate font-medium">{s.lender_name ?? t(`product_${s.product_type ?? "other_loan"}`)}</p>
                <p className="muted text-xs">
                  {t(`product_${s.product_type ?? "other_loan"}`)} · {new Date(s.created_at).toLocaleDateString(i18n.language)}</p>
              </div>
              <span className={STATE_BADGE[s.state]}>{t(`session_state_${s.state}`)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
