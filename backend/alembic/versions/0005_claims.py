"""Phase 1 / T1.6: claims, claim_values (doc 11 §3, doc 04 §4). Exactly the doc 11 DDL, plus lookup indexes.

Revision ID: 0005_claims
Revises: 0004_speaker_assignments
"""

from alembic import op

revision = "0005_claims"
down_revision = "0004_speaker_assignments"

UPGRADE = r"""
CREATE TABLE claims (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int NOT NULL, claim_type text, utterance_kind text, speaker_label text,
  normalized_statement text, quote text NOT NULL, quote_anchors jsonb NOT NULL, also_said_at jsonb DEFAULT '[]',
  start_ms int, end_ms int, conditions jsonb, vagueness text, extraction_confidence real,
  asr_avg_conf real, asr_min_numeric_conf real, quote_confirmed_by_user bool DEFAULT false,
  readiness text, missing_inputs text[], verifiable bool, state text DEFAULT 'active',
  supersedes_claim_id uuid, prompt_version text, created_at timestamptz DEFAULT now(), updated_at timestamptz);
CREATE INDEX ON claims (session_id, transcript_revision);
CREATE TABLE claim_values (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  field text, raw_text text, quantity text, value numeric, value_low numeric, value_high numeric, comparator text,
  period text, rate_basis text, rate_type text, of_what text, includes_gst bool, normaliser_version text);
CREATE INDEX ON claim_values (claim_id);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS claim_values;
DROP TABLE IF EXISTS claims;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
