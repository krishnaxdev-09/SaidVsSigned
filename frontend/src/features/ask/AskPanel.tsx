// T3.4 Ask Your Contract (lite, doc 09 §5–§6): question box with suggestions, answers with their verbatim citations,
// "not in your document" and fallback clauses. Updated live on `qa.answer` events.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, newIdempotencyKey } from "../../lib/api";

interface QA {
  qa_id: string; question: string; route: string | null; status: string;
  answer: { answer_text: string; citations: { chunk_id: string; quote: string }[]; not_in_document: boolean;
            fallback_clauses: { chunk_id: string; page: string; text: string }[] } | null;
}

export default function AskPanel({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["qa", sessionId], queryFn: () => api<QA[]>(`/api/v1/sessions/${sessionId}/qa`) });
  const sugg = useQuery({ queryKey: ["qa-suggestions", sessionId],
    queryFn: () => api<string[]>(`/api/v1/sessions/${sessionId}/qa/suggestions`) });

  async function send(question: string) {
    if (question.trim().length < 3) return;
    setError(null);
    try {
      await api(`/api/v1/sessions/${sessionId}/qa`, { method: "POST", body: { question }, idempotencyKey: newIdempotencyKey() });
      setQ("");
      await qc.invalidateQueries({ queryKey: ["qa", sessionId] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("ask_failed"));
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="ask-title">
      <h2 id="ask-title" className="font-semibold">{t("ask_title")}</h2>
      <p className="muted text-xs">{t("ask_help")}</p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void send(q); }}>
        <input aria-label={t("ask_title")} className="input mt-0 flex-1" value={q}
          onChange={(e) => setQ(e.target.value)} placeholder={t("ask_placeholder")} />
        <button className="btn-primary" type="submit">{t("ask_send")}</button>
      </form>
      <div className="flex flex-wrap gap-2">
        {(sugg.data ?? []).slice(0, 5).map((s) => (
          <button key={s} className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 transition hover:border-brand-500 hover:text-brand-700" onClick={() => send(s)}>{s}</button>))}
      </div>
      {error && <p role="alert" className="alert-error">{error}</p>}
      <ul className="space-y-3">
        {[...(list.data ?? [])].reverse().map((x) => (
          <li key={x.qa_id} data-testid="qa" className="card-tight space-y-1.5 text-sm">
            <p className="font-semibold">{x.question}</p>
            {!x.answer && <p className="text-slate-500">{t("ask_thinking")}</p>}
            {x.answer && <p>{x.answer.answer_text}</p>}
            {x.answer?.citations.map((c, i) => (
              <blockquote key={i} className="border-l-4 border-amber-400 pl-2 text-xs">“{c.quote}”</blockquote>))}
            {x.answer?.fallback_clauses.map((c) => (
              <blockquote key={c.chunk_id} className="border-l-4 border-slate-300 pl-2 text-xs">
                {t("page_n", { n: c.page })}: {c.text}</blockquote>))}
          </li>))}
      </ul>
    </section>
  );
}
