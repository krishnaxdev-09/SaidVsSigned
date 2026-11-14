// Mirrors doc 11 §4a. (Generated OpenAPI types replace this in a later phase; keep in sync until then.)
export type SessionState = "consent_pending" | "ready" | "processing" | "completed" | "failed" | "deleted";
export interface Session {
  id: string; mode: "upload" | "in_person" | "meet_tab"; state: SessionState; state_reason: string | null;
  product_type: string | null; language_hint: string | null; lender_name: string | null;
  retention_until: string; created_at: string;
}
export interface ConsentStatus {
  active: boolean; consent_text_version: string | null; granted_at: string | null; revoked_at: string | null;
  attestation: { i_was_party: boolean; agent_informed: "yes" | "no" | "unsure"; attested_at: string | null } | null;
  attestation_note: string | null; legal_caution: string | null;
}
export interface ConsentText { id: string; kind: "processing" | "attestation"; version: string; locale: string; body: string; body_sha256: string }
export interface EventEnvelope { type: string; session_id: string; seq: number; ts: string; payload: Record<string, unknown> }

export type RecordingStage = "uploaded" | "validating" | "normalising" | "transcribing" | "diarizing" | "ready" | "failed";
export interface Recording {
  recording_id: string; session_id: string; status: string; stage: RecordingStage; status_code: string | null;
  duration_ms: number | null; format: string | null; channel_layout: "mono" | "stereo_split" | null;
  warnings: string[]; replaced_by: string | null; created_at: string;
}
