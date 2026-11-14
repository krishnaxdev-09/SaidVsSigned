"""Phase 3 / T3.1: immutable calculation traces (doc 11 §3 DDL, doc 06 §7).

Revision ID: 0010_calculations
Revises: 0009_verification
"""

from alembic import op

revision = "0010_calculations"
down_revision = "0009_verification"


def upgrade() -> None:
    op.execute("""
CREATE TABLE calculations (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  formula_id text, formula_version int, inputs jsonb, outputs jsonb, assumptions text[], missing text[],
  tolerance_row text, enabled bool, created_at timestamptz DEFAULT now());
CREATE INDEX ON calculations (session_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calculations;")
