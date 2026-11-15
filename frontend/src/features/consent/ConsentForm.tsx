import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, newIdempotencyKey } from "../../lib/api";
import type { ConsentText } from "../../lib/types";

// Consent + customer attestation (doc 01 §5). The attestation is the customer's own statement, not proof of
// agent consent; the server returns the canonical note, the client never composes that wording.
export default function ConsentForm({ sessionId }: { sessionId: string }) {
  const { t, i18n } = useTranslation();
  const key = useRef(newIdempotencyKey());
  const texts = useQuery({ queryKey: ["consent-texts", i18n.language],
    queryFn: () => api<ConsentText[]>(`/api/v1/consent-texts?locale=${i18n.language}`) });
  const [accepted, setAccepted] = useState(false);
  const [party, setParty] = useState(false);
  const [informed, setInformed] = useState<"yes" | "no" | "unsure" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const processing = texts.data?.find((x) => x.kind === "processing");
  const attestation = texts.data?.find((x) => x.kind === "attestation");
  if (!processing || !attestation) return null;
  async function submit() {
    try {
      await api(`/api/v1/sessions/${sessionId}/consents`, { method: "POST", idempotencyKey: key.current, body: {
        consent_text_id: processing!.id, accepted,
        attestation: { attestation_text_id: attestation!.id, i_was_party: party, agent_informed: informed } } });
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
  }
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <p className="text-sm text-clarify-fg">{t("draft_notice")}</p>
      <h2 className="font-semibold">{t("consent_title")}</h2>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-sm text-slate-700">{processing.body}</pre>
      <label className="flex gap-2"><input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />{t("consent_accept")}</label>
      <h2 className="font-semibold">{t("attestation_title")}</h2>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-sm text-slate-700">{attestation.body}</pre>
      <label className="flex gap-2"><input type="checkbox" checked={party} onChange={(e) => setParty(e.target.checked)} />{t("i_was_party")}</label>
      <fieldset><legend>{t("agent_informed_q")}</legend>
        {(["yes", "no", "unsure"] as const).map((v) => (
          <label key={v} className="mr-4"><input type="radio" name="informed" checked={informed === v} onChange={() => setInformed(v)} /> {t(v)}</label>))}
      </fieldset>
      {error && <p role="alert" className="alert-error">{error}</p>}
      <button className="btn-primary" disabled={!accepted || !party || !informed} type="submit">{t("submit_consent")}</button>
    </form>
  );
}
