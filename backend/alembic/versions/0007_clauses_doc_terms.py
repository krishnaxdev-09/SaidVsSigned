"""Phase 2 / T2.3: clauses and doc_terms (doc 11 §3 DDL, doc 05 §6 steps 6–7).

`doc_terms.source` (lexicon | llm) is added so evaluation and the contract engine can tell the deterministic lexicon
pass from the LLM structuring pass. Values are always set by the normaliser (`normaliser_version`).

Revision ID: 0007_clauses_doc_terms
Revises: 0006_documents
"""

from alembic import op

revision = "0007_clauses_doc_terms"
down_revision = "0006_documents"

UPGRADE = r"""
CREATE TABLE clauses (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  page_start int, page_end int, bboxes jsonb, section_path text, label text, text text NOT NULL, is_table_row bool,
  text_source text, ocr_conf real, ocr_min_numeric_conf real, ord int);
CREATE INDEX clauses_doc_version ON clauses (document_id, version, ord);
CREATE TABLE doc_terms (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  clause_id uuid REFERENCES clauses ON DELETE CASCADE, field text, value_raw text, value numeric, unit text,
  basis text, rate_type text, conditions jsonb, includes_gst bool, normaliser_version text, source text);
CREATE INDEX doc_terms_doc_version ON doc_terms (document_id, version, field);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS doc_terms;
DROP TABLE IF EXISTS clauses;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
