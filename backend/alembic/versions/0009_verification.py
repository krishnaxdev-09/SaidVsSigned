"""Phase 2 / T2.5: verification runs, versioned results, evidence links, shadow verdicts (doc 11 §3 DDL).

Additions: `verification_results.thresholds_version` (doc 08 §3.4 VerificationResult carries it) and a unique partial
index so at most one result per claim is current.

Revision ID: 0009_verification
Revises: 0008_chunks
"""

from alembic import op

revision = "0009_verification"
down_revision = "0008_chunks"

UPGRADE = r"""
CREATE TABLE verification_runs (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  trigger text, thresholds_version text, features_version text, created_at timestamptz DEFAULT now());
CREATE TABLE verification_results (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  run_id uuid REFERENCES verification_runs ON DELETE CASCADE, version int, is_current bool,
  status text CHECK (status IN ('CONFLICT','MATCH','UNCLEAR','NOT_VERIFIED')), reason_code text, finding_kind text,
  headline text, reason text, severity text, primary_engine text, engine_verdicts jsonb,
  suggested_question text, changed_because jsonb NOT NULL, thresholds_version text,
  created_at timestamptz DEFAULT now(), UNIQUE (claim_id, version));
CREATE UNIQUE INDEX verification_results_current ON verification_results (claim_id) WHERE is_current;
CREATE TABLE evidence_links (id uuid PRIMARY KEY, result_id uuid REFERENCES verification_results ON DELETE CASCADE,
  kind text CHECK (kind IN ('spoken','document','calculation','rule','public')),
  quote text, quote_anchors jsonb, start_ms int, end_ms int, speaker_role text, role_assignment text,
  document_id uuid, document_version int, page int, clause_id uuid, clause_text text, bboxes jsonb, ocr_conf real,
  calculation_id uuid, rule_id text, rule_version int, source_url text, payload jsonb);
CREATE INDEX ON evidence_links (result_id);
CREATE TABLE shadow_verdicts (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  engine text, rule_id text, verdict jsonb, created_at timestamptz DEFAULT now());
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS shadow_verdicts;
DROP TABLE IF EXISTS evidence_links;
DROP TABLE IF EXISTS verification_results;
DROP TABLE IF EXISTS verification_runs;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
