"""Phase 1 / T1.1: audio_streams (doc 11 §3, doc 02).

Matches the doc 11 DDL, plus `warnings jsonb` (exposed by GET /recordings/{id}, doc 02 §4) and
`replaced_by` (set by PUT /recordings/{id}, doc 02 §4).

Revision ID: 0002_audio_streams
Revises: 0001_foundation
"""

from alembic import op

revision = "0002_audio_streams"
down_revision = "0001_foundation"

UPGRADE = r"""
CREATE TABLE audio_streams (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  source text DEFAULT 'upload', original_filename text, detected_format text, sha256 text NOT NULL,
  size_bytes bigint, duration_ms int, channel_layout text CHECK (channel_layout IN ('mono','stereo_split')),
  status text, status_code text, object_key_original text, object_key_normalized text,
  warnings jsonb DEFAULT '[]', replaced_by uuid,
  created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE UNIQUE INDEX audio_streams_session_sha ON audio_streams (session_id, sha256) WHERE deleted_at IS NULL;
CREATE INDEX ON audio_streams (session_id);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS audio_streams;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
