// T1.7 claims list (doc 10 §3 claims/ClaimList, ClaimCard): verbatim quote with playback, values as spoken and as
// understood, speaker badge (confirmed / auto-detected), why a claim is not checked, corrections and dismissal.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { mmss, playSnippet } from "../../lib/snippet";
import { PlayIcon } from "../results/status";
import type { Claim, ClaimValue } from "../../lib/types";

const VERIFIABLE = new Set(["promise", "assertion", "conditional"]);

export function valueText(v: ClaimValue): string {
  if (!v.normaliser_version) return "";
  const n = v.value_low !== null ? `${v.value_low}–${v.value_high}` : v.value;
  const unit = v.quantity === "percent" ? "%" : v.quantity === "months" ? " months" : "";
  return `${v.quantity === "amount_inr" ? "₹" : ""}${n}${unit}`;
}

export default function ClaimList({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [showDismissed, setShowDismissed] = useState(false);
  const q = useQuery({ queryKey: ["claims", sessionId], retry: false,
    queryFn: () => api<Claim[]>(`/api/v1/sessions/${sessionId}/claims?state=all`) });
  if (!q.data) return null;
  const claims = q.data.filter((c) => c.state === "active" || (showDismissed && c.state === "dismissed"));
  const correctedLater = new Set(q.data.map((c) => c.supersedes_claim_id).filter(Boolean));

  async function setState(id: string, state: "dismissed" | "active") {
    await api(`/api/v1/claims/${id}`, { method: "PATCH", body: { state } });
    await qc.invalidateQueries({ queryKey: ["claims", sessionId] });
  }

  return (
    <section className="space-y-3" aria-labelledby="claims-title">
      <div className="flex items-center justify-between">
        <h2 id="claims-title" className="font-semibold">{t("claims_title")}</h2>
        <label className="text-sm"><input type="checkbox" checked={showDismissed}
          onChange={(e) => setShowDismissed(e.target.checked)} /> {t("show_dismissed")}</label>
      </div>
      {claims.length === 0 && <p className="muted">{t("no_claims")}</p>}
      <ul className="space-y-2">
        {claims.map((c) => (
          <li key={c.id} data-testid="claim" className={`card-tight space-y-1.5 ${c.state === "dismissed" ? "opacity-60" : ""}`}>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <strong className="text-sm">{c.claim_type.replaceAll("_", " ")}</strong>
              <span className="badge-slate">
                {c.speaker_role === "agent" ? t("agent") : c.speaker_role === "customer" ? t("customer") : t("unknown_speaker")}
                {c.role_assignment === "auto" ? ` (${t("auto_detected")})` : ""}
              </span>
              {!VERIFIABLE.has(c.utterance_kind) && (
                <span className="badge-slate">{t("not_checked", { kind: t(`kind_${c.utterance_kind}`) })}</span>)}
              {c.vagueness === "vague" && <span className="badge-amber">{t("vague")}</span>}
              {c.vagueness === "approximate" && <span className="badge-slate">{t("approximate")}</span>}
              {c.supersedes_claim_id && <span className="badge-slate">{t("corrects_earlier")}</span>}
              {correctedLater.has(c.id) && <span className="badge-slate">{t("corrected_later")}</span>}
            </div>
            <p className="flex gap-2">
              <button aria-label={`play ${mmss(c.start_ms)}`} className="btn-play"
                onClick={() => playSnippet(sessionId, c.start_ms - 500, c.end_ms + 500)}><PlayIcon size={14} /></button>
              <span>“{c.quote}” <span className="text-slate-500">({mmss(c.start_ms)})</span></span>
            </p>
            {c.values.map((v, i) => (
              <p key={i} className="text-sm" data-testid="claim-value">
                “{v.raw_text}” → {v.normaliser_version ? <strong>{valueText(v)}</strong> : <em>{t("unparsed")}</em>}
              </p>
            ))}
            {c.conditions.map((cd, i) => <p key={i} className="muted">{t("condition")}: {cd.text}</p>)}
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>{c.verifiable && c.readiness === "needs_document" ? t("needs_document") : ""}</span>
              <button className="link" onClick={() => setState(c.id, c.state === "dismissed" ? "active" : "dismissed")}>
                {c.state === "dismissed" ? t("restore") : t("dismiss")}</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
