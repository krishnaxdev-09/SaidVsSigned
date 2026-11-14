import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import ConsentForm from "../consent/ConsentForm";
import RecordingPanel from "../recordings/RecordingPanel";
import SpeakerConfirm from "../speakers/SpeakerConfirm";
import ClaimList from "../claims/ClaimList";
import TranscriptView from "../transcript/TranscriptView";
import DocumentUpload from "../documents/DocumentUpload";
import ResultsDashboard from "../results/ResultsDashboard";
import AskPanel from "../ask/AskPanel";
import { api } from "../../lib/api";
import { connectEvents } from "../../lib/events";
import { applyEvent } from "../../lib/eventLog";
import type { ConsentStatus, EventEnvelope, Session } from "../../lib/types";

export default function SessionPage() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["state", id],
    queryFn: () => api<{ session: Session; consent: ConsentStatus; last_event_seq: number }>(`/api/v1/sessions/${id}/state`) });
  const log = useRef({ lastSeq: 0, events: [] as EventEnvelope[] });
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  useEffect(() => connectEvents(id, () => log.current.lastSeq, (ev) => {
    log.current = applyEvent(log.current, ev);
    setEvents(log.current.events);
    qc.invalidateQueries({ queryKey: ["state", id] });
    if (ev.type === "recording.status") qc.invalidateQueries({ queryKey: ["recording", id] });
    if (ev.type === "transcript.ready" || ev.type === "speaker.updated") qc.invalidateQueries({ queryKey: ["speakers", id] });
    if (ev.type === "transcript.ready") qc.invalidateQueries({ queryKey: ["transcript", id] });
    if (ev.type.startsWith("claim.") || ev.type === "speaker.updated") qc.invalidateQueries({ queryKey: ["claims", id] });
    if (ev.type === "document.status") qc.invalidateQueries({ queryKey: ["documents", id] });
    if (ev.type === "verification.updated") qc.invalidateQueries({ queryKey: ["results", id] });
    if (ev.type === "qa.answer") qc.invalidateQueries({ queryKey: ["qa", id] });
  }), [id, qc]);
  if (!q.data) return null;
  const { session, consent } = q.data;
  return (
    <div className="space-y-4">
      <p>{t("state")}: <strong data-testid="state">{session.state}</strong>{session.state_reason ? ` (${session.state_reason})` : ""}</p>
      {session.state === "consent_pending" && <ConsentForm sessionId={id} />}
      {consent.attestation_note && <p className="text-sm">{consent.attestation_note}</p>}
      {consent.active && <RecordingPanel sessionId={id} />}
      {consent.active && <SpeakerConfirm sessionId={id} />}
      {consent.active && <ResultsDashboard sessionId={id} />}
      {consent.active && <DocumentUpload sessionId={id} />}
      {consent.active && <AskPanel sessionId={id} />}
      {consent.active && <ClaimList sessionId={id} />}
      {consent.active && <TranscriptView sessionId={id} />}
      {consent.active && (
        <>
          <button className="rounded border border-red-700 px-3 py-2 text-red-700"
            onClick={() => api(`/api/v1/sessions/${id}/consents/customer`, { method: "DELETE" })}>{t("revoke")}</button>
        </>
      )}
      <ul aria-label="events" className="text-xs text-slate-500">{events.map((e) => <li key={e.seq}>#{e.seq} {e.type}</li>)}</ul>
    </div>
  );
}
