"""Phase 2 / T2.4: chunks for hybrid retrieval (doc 11 §3 DDL, doc 09 §3).

The embedding dimension is a migration parameter (doc 09 §3): EMBEDDING_DIM from settings, ADR-005 working choice
`intfloat/multilingual-e5-small` → 384 (provisional until HC-4).

Revision ID: 0008_chunks
Revises: 0007_clauses_doc_terms
"""

import os

from alembic import op

revision = "0008_chunks"
down_revision = "0007_clauses_doc_terms"


def upgrade() -> None:
    dim = int(os.environ.get("EMBEDDING_DIM") or 384)  # same default as Settings.EMBEDDING_DIM
    op.execute(f"""
CREATE TABLE chunks (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  clause_id uuid REFERENCES clauses ON DELETE CASCADE, page int, section_path text, text text,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(section_path,'')||' '||text)) STORED,
  embedding vector({dim}), embedding_model text);
CREATE INDEX ON chunks USING gin (tsv);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_doc_version ON chunks (document_id, version);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks;")
