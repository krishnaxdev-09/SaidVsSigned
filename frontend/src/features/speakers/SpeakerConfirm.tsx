// T1.3 "Who is the agent?" (doc 10 §5, doc 03 §5): 3 quotes per speaker with play buttons, one-tap confirm or swap.
// Until the customer confirms, possible mismatches can only show as "needs clarification" (doc 08 §5).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../../lib/api";
import { mmss as fmt, playSnippet } from "../../lib/snippet";
import type { Speaker, Speakers } from "../../lib/types";
import { confidentSuggestedAgent, confirmedAgent, rolesForAgent } from "./roles";

export default function SpeakerConfirm({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["speakers", sessionId], retry: false,
    queryFn: () => api<Speakers>(`/api/v1/sessions/${sessionId}/speakers`) });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  if (!q.data) return null; // no transcript yet (404) or still loading
  const data = q.data;
  const labels = data.speakers.map((s) => s.speaker_label);
  const name = (s: Speaker) => s.speaker_source === "channel"
    ? t("speaker_side", { side: s.speaker_label }) : t("speaker_n", { n: labels.indexOf(s.speaker_label) + 1 });
  const nameOf = (label: string) => name(data.speakers.find((s) => s.speaker_label === label)!);
  const agent = confirmedAgent(data.speakers);
  const suggested = confidentSuggestedAgent(data.speakers);

  async function choose(agentLabel: string) {
    setBusy(true); setMessage(null);
    try {
      await api(`/api/v1/sessions/${sessionId}/speakers`, { method: "PUT",
        body: { transcript_revision: data.transcript_revision, roles: rolesForAgent(labels, agentLabel) } });
      setEditing(false);
    } catch (e) {
      setMessage(e instanceof ApiError && e.code === "invalid_state" ? t("transcript_changed") : t("save_failed"));
    } finally {
      setBusy(false);
      // Refresh roles everywhere they show, without waiting for the speaker.updated event.
      await Promise.all(["speakers", "claims", "transcript"].map((k) => qc.invalidateQueries({ queryKey: [k, sessionId] })));
    }
  }

  const play = (fromMs: number, toMs: number) => playSnippet(sessionId, fromMs, toMs);

  if (agent && !editing) {
    return (
      <section className="flex items-center justify-between rounded border border-emerald-300 bg-emerald-50 p-3"
        data-testid="agent-confirmed">
        <span>✓ {t("agent_confirmed", { n: labels.indexOf(agent) + 1 })}</span>
        <button className="text-sm underline" onClick={() => setEditing(true)}>{t("change")}</button>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded border border-slate-200 p-4" aria-labelledby="spk-title">
      <h2 id="spk-title" className="font-semibold">{t("speakers_title")}</h2>
      <p role="status" className="rounded bg-amber-50 p-2 text-sm text-amber-900">{t("confirm_banner")}</p>
      <p className="text-sm text-slate-600">{data.speakers.length === 1 ? t("single_speaker") : t("speakers_help")}</p>
      {suggested && (
        <button className="w-full rounded bg-slate-900 px-3 py-3 text-white disabled:opacity-50" disabled={busy}
          onClick={() => choose(suggested)}>{t("confirm_suggestion", { n: labels.indexOf(suggested) + 1 })}</button>
      )}
      <ul className="space-y-3">
        {data.speakers.map((s) => (
          <li key={s.speaker_label} className="space-y-2 rounded border border-slate-200 p-3">
            <div className="flex items-center justify-between">
              <strong>{name(s)}</strong>
              {s.suggested_role && s.suggested_role !== "unknown" && (
                <span className={`rounded px-2 py-0.5 text-xs ${s.suggestion_uncertain ? "bg-amber-100" : "bg-slate-100"}`}>
                  {s.suggestion_uncertain ? t("suggestion_unsure")
                    : s.suggested_role === "agent" ? t("suggested_agent") : t("suggested_customer")}
                </span>
              )}
            </div>
            <ul className="space-y-1 text-sm">
              {s.quotes.map((qt) => (
                <li key={qt.segment_id} className="flex gap-2">
                  <button aria-label={`${t("play")} ${fmt(qt.start_ms)}`} className="h-11 min-w-11 rounded border px-2"
                    onClick={() => play(qt.start_ms, qt.end_ms)}>▶</button>
                  <span>“{qt.text}” <span className="text-slate-500">({fmt(qt.start_ms)})</span></span>
                </li>
              ))}
            </ul>
            <button className="rounded border border-slate-900 px-3 py-2 disabled:opacity-50" disabled={busy}
              onClick={() => choose(s.speaker_label)}>{t("this_is_agent")}: {nameOf(s.speaker_label)}</button>
          </li>
        ))}
      </ul>
      {message && <p role="alert" className="text-sm text-red-700">{message}</p>}
    </section>
  );
}
