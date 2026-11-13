import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import ConsentForm from "../consent/ConsentForm";
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
  }), [id, qc]);
  if (!q.data) return null;
  const { session, consent } = q.data;
  return (
    <div className="space-y-4">
      <p>{t("state")}: <strong data-testid="state">{session.state}</strong>{session.state_reason ? ` (${session.state_reason})` : ""}</p>
      {session.state === "consent_pending" && <ConsentForm sessionId={id} />}
      {consent.attestation_note && <p className="text-sm">{consent.attestation_note}</p>}
      {session.state === "ready" && (
        <>
          <p className="text-slate-600">{t("upload_next")}</p>
          <button className="rounded border border-red-700 px-3 py-2 text-red-700"
            onClick={() => api(`/api/v1/sessions/${id}/consents/customer`, { method: "DELETE" })}>{t("revoke")}</button>
        </>
      )}
      <ul aria-label="events" className="text-xs text-slate-500">{events.map((e) => <li key={e.seq}>#{e.seq} {e.type}</li>)}</ul>
    </div>
  );
}
