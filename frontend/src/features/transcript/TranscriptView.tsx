// T1.7 transcript view (doc 10 §3 transcript/TranscriptView): timestamps, speaker chips, play, and the per-segment
// speaker fix (doc 03 §5 step 4; backend PATCH /segments/{id}/speaker from T1.3).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { mmss, playSnippet } from "../../lib/snippet";
import type { SpeakerRole, Speakers, Transcript } from "../../lib/types";

export default function TranscriptView({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tq = useQuery({ queryKey: ["transcript", sessionId], retry: false,
    queryFn: () => api<Transcript>(`/api/v1/sessions/${sessionId}/transcript`) });
  const sq = useQuery({ queryKey: ["speakers", sessionId], retry: false,
    queryFn: () => api<Speakers>(`/api/v1/sessions/${sessionId}/speakers`) });
  if (!tq.data) return null;
  const roleOf = new Map(sq.data?.speakers.map((s) => [s.speaker_label, s]) ?? []);
  const override = new Map(sq.data?.overrides.map((o) => [o.segment_id, o.speaker_role]) ?? []);
  const label = (r: SpeakerRole) => (r === "agent" ? t("agent") : r === "customer" ? t("customer") : t("unknown_speaker"));

  async function fix(segmentId: string, role: SpeakerRole) {
    await api(`/api/v1/segments/${segmentId}/speaker`, { method: "PATCH", body: { speaker_role: role } });
    await Promise.all([qc.invalidateQueries({ queryKey: ["speakers", sessionId] }),
      qc.invalidateQueries({ queryKey: ["claims", sessionId] })]);
  }

  return (
    <section className="space-y-2" aria-labelledby="tr-title">
      <h2 id="tr-title" className="font-semibold">{t("transcript_title")}</h2>
      <ol className="space-y-2">
        {tq.data.segments.map((g) => {
          const sp = roleOf.get(g.speaker_label ?? "unknown");
          const role = override.get(g.id) ?? sp?.speaker_role ?? "unknown";
          const auto = !override.has(g.id) && sp?.role_assignment !== "user_confirmed";
          return (
            <li key={g.id} className="flex gap-2 text-sm" data-testid="segment">
              <button aria-label={`play ${mmss(g.start_ms)}`} className="btn-play"
                onClick={() => playSnippet(sessionId, g.start_ms, g.end_ms)}>▶</button>
              <div className="flex-1">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span>{mmss(g.start_ms)}</span>
                  <span className="badge-slate" data-testid="speaker-chip">
                    {label(role)}{auto ? ` (${t("auto_detected")})` : ""}</span>
                  <select aria-label={t("fix_speaker")} className="text-xs" value=""
                    onChange={(e) => e.target.value && fix(g.id, e.target.value as SpeakerRole)}>
                    <option value="">{t("fix_speaker")}</option>
                    <option value="agent">{t("agent")}</option>
                    <option value="customer">{t("customer")}</option>
                  </select>
                </div>
                <p>{g.text}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
