"""Live microphone capture (user request 2026-11-15; capture-only slice of Later-L1, verify on stop).

The browser streams MediaRecorder chunks while the customer talks; on Stop they are assembled in order and handed to
the existing recording pipeline as one audio stream (audio_streams.source = 'live_mic'). No partial results.

Revision ID: 0013_live_captures
Revises: 0012_rules_public_data
"""

from alembic import op

revision = "0013_live_captures"
down_revision = "0012_rules_public_data"


def upgrade() -> None:
    op.execute("""
CREATE TABLE live_captures (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  status text CHECK (status IN ('open','finished','aborted')), mime text, bytes bigint DEFAULT 0,
  audio_stream_id uuid, created_at timestamptz DEFAULT now(), finished_at timestamptz);
CREATE TABLE live_chunks (live_id uuid REFERENCES live_captures ON DELETE CASCADE, seq int, size int, sha256 text,
  object_key text, created_at timestamptz DEFAULT now(), PRIMARY KEY (live_id, seq));
CREATE INDEX ON live_captures (session_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_chunks; DROP TABLE IF EXISTS live_captures;")
