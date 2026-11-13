"""Phase 0 foundation tables — mirrors doc 11 §3 (v0.2.2) for: users, refresh_tokens, ws_tickets, sessions,
consent_texts, consents, idempotency_keys, jobs, events, audit_log, deletion_receipts.

Revision ID: 0001_foundation
Revises:
"""

from alembic import op

revision = "0001_foundation"
down_revision = None

UPGRADE = r"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (id uuid PRIMARY KEY, email citext UNIQUE NOT NULL, password_hash text NOT NULL,
  locale text DEFAULT 'en', created_at timestamptz DEFAULT now(), deleted_at timestamptz);

CREATE TABLE refresh_tokens (id uuid PRIMARY KEY, user_id uuid REFERENCES users NOT NULL, family_id uuid NOT NULL,
  token_hash text UNIQUE NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz, replaced_by uuid,
  created_at timestamptz DEFAULT now());
CREATE INDEX ON refresh_tokens (family_id);

CREATE TABLE sessions (id uuid PRIMARY KEY, owner_id uuid REFERENCES users NOT NULL,
  mode text NOT NULL CHECK (mode IN ('upload','in_person','meet_tab')),
  state text NOT NULL CHECK (state IN ('consent_pending','ready','processing','completed','failed','deleted')),
  state_reason text,
  product_type text CHECK (product_type IN ('personal_loan','home_loan','vehicle_loan','business_loan','gold_loan','other_loan')),
  language_hint text CHECK (language_hint IN ('en','hi','hinglish')),
  lender_entity_id uuid, lender_name_raw text, facts jsonb DEFAULT '{}',
  retention_until timestamptz NOT NULL, created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE INDEX ON sessions (owner_id, id DESC);

CREATE TABLE ws_tickets (ticket_hash text PRIMARY KEY, user_id uuid REFERENCES users NOT NULL,
  session_id uuid REFERENCES sessions NOT NULL, purpose text CHECK (purpose IN ('events','audio')),
  expires_at timestamptz NOT NULL, used_at timestamptz);

CREATE TABLE consent_texts (id uuid PRIMARY KEY, kind text CHECK (kind IN ('processing','attestation')),
  version text, locale text, body text NOT NULL, body_sha256 text NOT NULL, effective_from timestamptz,
  retired_at timestamptz, UNIQUE (kind, version, locale));

CREATE TABLE consents (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions,
  party text CHECK (party IN ('customer','agent')),
  method text, consent_text_id uuid REFERENCES consent_texts, consent_text_version text, consent_text_sha256 text,
  locale text, granted_at timestamptz NOT NULL, revoked_at timestamptz, actor_id uuid, client_meta jsonb,
  attestation jsonb,
  attestation_text_id uuid REFERENCES consent_texts, attestation_text_sha256 text, attested_at timestamptz);
CREATE INDEX ON consents (session_id);
-- at most one active (non-revoked) customer consent per session
CREATE UNIQUE INDEX consents_one_active_customer ON consents (session_id) WHERE party = 'customer' AND revoked_at IS NULL;

CREATE TABLE idempotency_keys (user_id uuid REFERENCES users, key text, method text, path text, request_hash text,
  status_code int, response_body jsonb, created_at timestamptz DEFAULT now(), PRIMARY KEY (user_id, key));

CREATE TABLE events (seq bigserial PRIMARY KEY, session_id uuid NOT NULL, type text, payload jsonb, ts timestamptz DEFAULT now());
CREATE INDEX ON events (session_id, seq);

CREATE TABLE jobs (id uuid PRIMARY KEY, session_id uuid, kind text, dedupe_key text, payload jsonb,
  status text CHECK (status IN ('queued','running','succeeded','failed','cancelled')), attempts int DEFAULT 0,
  error_class text, run_after timestamptz DEFAULT now(), locked_by text, locked_at timestamptz, last_error text,
  cancel_requested_at timestamptz, lease_expires_at timestamptz, finished_at timestamptz,
  created_at timestamptz DEFAULT now());
CREATE UNIQUE INDEX jobs_dedupe_queued ON jobs (dedupe_key) WHERE status = 'queued';
CREATE INDEX ON jobs (status, run_after);
CREATE INDEX ON jobs (session_id);
CREATE INDEX ON jobs (dedupe_key) WHERE status = 'running';

CREATE TABLE audit_log (id bigserial PRIMARY KEY, actor_id uuid, action text, target_type text, target_id uuid,
  meta jsonb, ip inet, ts timestamptz DEFAULT now());
CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

CREATE TABLE deletion_receipts (id uuid PRIMARY KEY, session_id uuid, owner_id uuid, requested_at timestamptz,
  completed_at timestamptz, objects_deleted int, rows_deleted jsonb);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS deletion_receipts;
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log;
DROP TABLE IF EXISTS audit_log;
DROP FUNCTION IF EXISTS audit_log_append_only();
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS consents;
DROP TABLE IF EXISTS consent_texts;
DROP TABLE IF EXISTS ws_tickets;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS refresh_tokens;
DROP TABLE IF EXISTS users;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
