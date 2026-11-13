import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import type { Session } from "../../lib/types";

export default function SessionListPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => api<{ items: Session[] }>("/api/v1/sessions") });
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">{t("sessions")}</h1>
        <Link className="rounded bg-slate-800 px-3 py-2 text-white" to="/sessions/new">{t("new_session")}</Link>
      </div>
      <ul className="divide-y rounded border">
        {q.data?.items.map((s) => (
          <li key={s.id} className="p-3"><Link className="underline" to={`/sessions/${s.id}`}>{s.lender_name ?? s.product_type}</Link>
            <span className="ml-2 text-sm text-slate-600">{t("state")}: {s.state}</span></li>
        ))}
      </ul>
    </div>
  );
}
