"""Phase 1 / T1.2: transcript_revisions, transcript_segments, transcript_words (doc 11 §3, doc 03 §6).

Exactly the doc 11 DDL. A transcript_revisions row with provider NULL is a revision that has been allocated for a
recording but not transcribed yet (the stt.batch job fills it in).

Revision ID: 0003_transcripts
Revises: 0002_audio_streams
"""

from alembic import op

revision = "0003_transcripts"
down_revision = "0002_audio_streams"

UPGRADE = r"""
CREATE TABLE transcript_revisions (session_id uuid REFERENCES sessions ON DELETE CASCADE, revision int,
  audio_stream_id uuid REFERENCES audio_streams, provider text, provider_config jsonb, is_current bool,
  created_at timestamptz DEFAULT now(), PRIMARY KEY (session_id, revision));
CREATE TABLE transcript_segments (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int NOT NULL, channel text CHECK (channel IN ('mono','L','R')), speaker_label text,
  text text NOT NULL, start_ms int, end_ms int, language text, asr_avg_conf real, asr_min_numeric_conf real);
CREATE INDEX ON transcript_segments (session_id, transcript_revision, start_ms);
CREATE TABLE transcript_words (segment_id uuid REFERENCES transcript_segments ON DELETE CASCADE, idx int,
  text text, start_ms int, end_ms int, confidence real, speaker_label text, is_numeric bool, PRIMARY KEY (segment_id, idx));
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS transcript_words;
DROP TABLE IF EXISTS transcript_segments;
DROP TABLE IF EXISTS transcript_revisions;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
