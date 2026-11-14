"""Phase 2 / T2.1: documents, document_pages (doc 11 §3, doc 05 §4–§6).

Doc 11 DDL plus two columns on document_pages, `original_key` and `original_sha256`, so a page replace can build the
next version (and its content hash) without re-reading every page.

Revision ID: 0006_documents
Revises: 0005_claims
"""

from alembic import op

revision = "0006_documents"
down_revision = "0005_claims"

UPGRADE = r"""
CREATE TABLE documents (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  kind text, source text CHECK (source IN ('pdf','photos')), version int DEFAULT 1, content_sha256 text NOT NULL,
  status text, status_code text, page_count int, warnings jsonb DEFAULT '[]', created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE UNIQUE INDEX documents_session_sha ON documents (session_id, content_sha256) WHERE deleted_at IS NULL;
CREATE INDEX ON documents (session_id);
CREATE TABLE document_pages (document_id uuid REFERENCES documents ON DELETE CASCADE, version int, page int,
  printed_page text, image_key text, width real, height real, text_source text, ocr_conf real, quality jsonb,
  original_key text, original_sha256 text,
  PRIMARY KEY (document_id, version, page));
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS document_pages;
DROP TABLE IF EXISTS documents;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
