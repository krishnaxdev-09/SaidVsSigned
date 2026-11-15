// T1.3 "Who is the agent?" (doc 10 §5, doc 03 §5): 3 quotes per speaker with play buttons, one-tap confirm or swap.
// Until the customer confirms, possible mismatches can only show as "needs clarification" (doc 08 §5).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../../lib/api";
import { mmss as fmt, playSnippet } from "../../lib/snippet";
import type { Speaker, Speakers } from "../../lib/types";
import { PlayIcon } from "../results/status";
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
      <section className="alert-ok flex items-center justify-between"
        data-testid="agent-confirmed">
        <span className="font-medium">✓ {t("agent_confirmed", { n: labels.indexOf(agent) + 1 })}</span>
        <button className="link text-sm" onClick={() => setEditing(true)}>{t("change")}</button>
      </section>
    );
  }

  return (
    <section className="space-y-3" aria-labelledby="spk-title">
      <h2 id="spk-title" className="font-semibold">{t("speakers_title")}</h2>
      <p role="status" className="alert-warn">{t("confirm_banner")}</p>
      <p className="muted">{data.speakers.length === 1 ? t("single_speaker") : t("speakers_help")}</p>
      {suggested && (
        <button className="btn-primary w-full" disabled={busy}
          onClick={() => choose(suggested)}>{t("confirm_suggestion", { n: labels.indexOf(suggested) + 1 })}</button>
      )}
      <ul className="grid gap-4 sm:grid-cols-2">
        {data.speakers.map((s) => (
          <li key={s.speaker_label} className={`flex flex-col gap-3.5 rounded-2xl border bg-white p-4 ${
            s.speaker_label === suggested ? "border-brand-300" : "border-slate-200"}`}>
            <div className="flex items-center justify-between gap-2">
              <strong className="text-[17px]">{name(s)}</strong>
              {s.suggested_role && s.suggested_role !== "unknown" && (
                <span className={s.suggestion_uncertain ? "badge-amber" : s.suggested_role === "agent" ? "badge-brand" : "badge-slate"}>
                  {s.suggestion_uncertain ? t("suggestion_unsure")
                    : s.suggested_role === "agent" ? t("suggested_agent") : t("suggested_customer")}
                </span>
              )}
            </div>
            <ul className="flex-1 space-y-2 text-sm">
              {s.quotes.map((qt) => (
                <li key={qt.segment_id} className="flex items-center gap-2.5">
                  <button aria-label={`${t("play")} ${fmt(qt.start_ms)}`} className="btn-play"
                    onClick={() => play(qt.start_ms, qt.end_ms)}><PlayIcon size={14} /></button>
                  <span className="min-w-0 leading-snug">“{qt.text}” <span className="font-mono text-xs text-slate-500">{fmt(qt.start_ms)}</span></span>
                </li>
              ))}
            </ul>
            <button className="btn-secondary" disabled={busy}
              onClick={() => choose(s.speaker_label)}>{t("this_is_agent")}: {nameOf(s.speaker_label)}</button>
          </li>
        ))}
      </ul>
      {message && <p role="alert" className="alert-error">{message}</p>}
    </section>
  );
}
