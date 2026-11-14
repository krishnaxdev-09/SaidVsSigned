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

export type SpeakerRole = "agent" | "customer" | "unknown";
export interface SpeakerQuote { segment_id: string; text: string; start_ms: number; end_ms: number }
export interface Speaker {
  speaker_label: string; speaker_source: "diarized" | "channel"; segment_count: number;
  suggested_role: SpeakerRole | null; role_confidence: number | null; suggestion_uncertain: boolean;
  speaker_role: SpeakerRole; role_assignment: "auto" | "user_confirmed"; confirmed_at: string | null;
  quotes: SpeakerQuote[];
}
export interface Speakers {
  transcript_revision: number; confirmed: boolean; speakers: Speaker[];
  overrides: { segment_id: string; speaker_role: SpeakerRole }[];
}

export interface Word { text: string; start_ms: number; end_ms: number; confidence: number | null; speaker_label: string | null; is_numeric: boolean }
export interface Segment {
  id: string; channel: "mono" | "L" | "R"; speaker_label: string | null; text: string; start_ms: number; end_ms: number;
  words: Word[]; language: string | null; asr_avg_conf: number | null; asr_min_numeric_conf: number | null;
}
export interface Transcript { transcript_revision: number; is_current: boolean; audio_stream_id: string | null; provider: string | null; segments: Segment[] }
export interface ClaimValue {
  field: string; raw_text: string; quantity: string; value: string | null; value_low: string | null; value_high: string | null;
  comparator: string; period: string; rate_basis: string | null; rate_type: string | null; includes_gst: boolean | null;
  normaliser_version: string | null;
}
export interface Claim {
  id: string; transcript_revision: number; claim_type: string; utterance_kind: string; speaker_label: string | null;
  speaker_role: SpeakerRole; role_assignment: "auto" | "user_confirmed"; normalized_statement: string; quote: string;
  start_ms: number; end_ms: number; values: ClaimValue[]; vagueness: "specific" | "approximate" | "vague";
  readiness: string; verifiable: boolean; state: "active" | "dismissed" | "superseded"; supersedes_claim_id: string | null;
  conditions: { text: string; kind: string }[];
}
