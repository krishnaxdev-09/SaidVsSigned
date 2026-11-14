"""Phase 3 / T3.3 + T3.4: reports and qa_messages (doc 11 §3 DDL).

Revision ID: 0011_reports_qa
Revises: 0010_calculations
"""

from alembic import op

revision = "0011_reports_qa"
down_revision = "0010_calculations"


def upgrade() -> None:
    op.execute("""
CREATE TABLE reports (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  status text, object_key text, result_versions jsonb, created_at timestamptz DEFAULT now());
CREATE TABLE qa_messages (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  question text, route text, answer jsonb, calculation_id uuid, status text, created_at timestamptz DEFAULT now());
CREATE INDEX ON reports (session_id);
CREATE INDEX ON qa_messages (session_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qa_messages; DROP TABLE IF EXISTS reports;")
