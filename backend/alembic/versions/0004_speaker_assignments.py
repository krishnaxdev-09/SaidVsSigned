"""Phase 1 / T1.3: speaker_assignments (doc 11 §3, doc 03 §5). Exactly the doc 11 DDL, plus lookup indexes.

Revision ID: 0004_speaker_assignments
Revises: 0003_transcripts
"""

from alembic import op

revision = "0004_speaker_assignments"
down_revision = "0003_transcripts"

UPGRADE = r"""
CREATE TABLE speaker_assignments (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int, speaker_label text, segment_id uuid NULL,
  speaker_role text CHECK (speaker_role IN ('agent','customer','unknown')),
  speaker_source text CHECK (speaker_source IN ('diarized','channel','user','inferred')),
  role_assignment text CHECK (role_assignment IN ('auto','user_confirmed')), role_confidence real,
  confirmed_by uuid, confirmed_at timestamptz, is_current bool DEFAULT true);
CREATE INDEX ON speaker_assignments (session_id, transcript_revision) WHERE is_current;
CREATE INDEX ON speaker_assignments (segment_id) WHERE segment_id IS NOT NULL;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS speaker_assignments;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
