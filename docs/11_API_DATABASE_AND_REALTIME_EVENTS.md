# 11 — API, Database & Events (reference)

> **Version 0.2.2 — 2026-11-13 (0.2.1: consent tables and job cancellation; 0.2.2: implementation audit fixes A1–A18).** Original: `_archive/v0.1-2026-11-13/11_…md`.
> **Scope:** [MVP] REST, events WS, schema, idempotency. [Deferred] audio WS and `transcript.partial` (Later-L1).
>
> **Canonical here:** endpoint list, **idempotency & duplicate processing (§2)**, the event type list (§5) and the DDL (§3). Pydantic models are canonical in docs 04 and 08; the columns below mirror them exactly.

## 1. Conventions
- Base path `/api/v1`. UUIDv7 ids. JSON.
- Errors: doc 01 §7.
- Retry classes: doc 01 §6.
- Pagination `?cursor=&limit=` (≤ 200). Times in UTC ISO-8601. Media offsets in ms from the start of the recording.

## 2. Idempotency & duplicate processing (canonical)
| Layer | Mechanism |
|---|---|
| HTTP POST/PUT that create resources (sessions, recordings, documents, qa, reports, consents) | `Idempotency-Key` header **required**. Stored in `idempotency_keys(user_id, key, method, path, request_hash, status_code, response_body, created_at)` with a 24 h TTL. `request_hash` = sha256(method + path + canonical body). Same key + same hash → replay the stored response. Same key + different hash → `409 conflict_idempotency_key`. Missing header → `400 idempotency_key_required`. In-flight (row exists, `status_code` NULL, `created_at` < 60 s ago) → `409 idempotency_in_progress` with `Retry-After: 2`. In-flight rows older than 60 s are treated as abandoned and may be retried. Only 2xx responses are stored; on 4xx/5xx the key row is deleted so the client can retry (A2) |
| Content dedupe | `audio_streams` UNIQUE `(session_id, sha256) WHERE deleted_at IS NULL`; `documents` UNIQUE `(session_id, content_sha256) WHERE deleted_at IS NULL`. Duplicates return the existing id with `duplicate:true` |
| Jobs | **Coalescing dedupe** (A7): partial UNIQUE index on `dedupe_key` `WHERE status = 'queued'`. `enqueue()` is `INSERT … ON CONFLICT (dedupe_key) WHERE status='queued' DO NOTHING`. The claim query skips a queued job if a job with the same key is `running`. Result: at most one queued and one running per key, and a trigger arriving during a run produces exactly one follow-up (v0.2.1's index over queued+running would have dropped that trigger, so a re-verification could be lost). `enqueue()` also refuses session-scoped jobs when the session's customer consent is revoked. **Lease** (A8): `lease_expires_at`; expired running jobs are reclaimed as a transient attempt. Keys below |
| Job handlers | At-least-once delivery. Each handler writes derived rows for its `(parent, revision/version)` **inside one transaction**, deleting that version's previous rows first, so re-runs replace rather than duplicate. Object keys are deterministic. Events are published **after commit** |
| Events | `seq` is monotonic. Clients ignore `seq ≤ last_seen` |
| Verification | One new result version per run; `verify:{claim_id}` dedupe plus a 2 s debounce |

**Job kinds and dedupe keys:**

| Kind | Dedupe key | Doc |
|---|---|---|
| `audio.ingest` | `audio.ingest:{audio_stream_id}` | 02 |
| `stt.batch` | `stt.batch:{audio_stream_id}:{transcript_revision}` | 03 |
| `speakers.suggest` | `speakers.suggest:{session_id}:{transcript_revision}` | 03 |
| `claims.extract` | `claims.extract:{session_id}:{transcript_revision}` | 04 |
| `doc.ingest` | `doc.ingest:{document_id}:{version}` | 05 |
| `verify` | `verify:{claim_id}` | 08 |
| `qa.answer` | `qa:{qa_id}` | 09 |
| `report.render` | `report:{report_id}` | 10/12 |
| `session.erase` | `erase:{session_id}` | 12 |
| `retention.purge` | `purge:{date}` | 12 |

## 3. Database (PostgreSQL 16; extensions `vector`, `pgcrypto`, `pg_trgm`, `citext`)
```sql
-- identity, sessions, consent
CREATE TABLE users (id uuid PRIMARY KEY, email citext UNIQUE NOT NULL, password_hash text NOT NULL,
  locale text DEFAULT 'en', created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE TABLE refresh_tokens (id uuid PRIMARY KEY, user_id uuid REFERENCES users NOT NULL, family_id uuid NOT NULL,
  token_hash text UNIQUE NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz, replaced_by uuid,
  created_at timestamptz DEFAULT now());   -- rotation; reuse of a revoked token revokes the whole family (A12)
CREATE TABLE ws_tickets (ticket_hash text PRIMARY KEY, user_id uuid REFERENCES users NOT NULL, session_id uuid REFERENCES sessions NOT NULL,
  purpose text CHECK (purpose IN ('events','audio')), expires_at timestamptz NOT NULL, used_at timestamptz);   -- 'audio' Later-L1
CREATE TABLE sessions (id uuid PRIMARY KEY, owner_id uuid REFERENCES users NOT NULL,
  mode text NOT NULL CHECK (mode IN ('upload','in_person','meet_tab')),   -- only 'upload' accepted in MVP
  state text NOT NULL CHECK (state IN ('consent_pending','ready','processing','completed','failed','deleted')),
  state_reason text,
  product_type text CHECK (product_type IN ('personal_loan','home_loan','vehicle_loan','business_loan','gold_loan','other_loan')),
  language_hint text CHECK (language_hint IN ('en','hi','hinglish')),
  lender_entity_id uuid, lender_name_raw text, facts jsonb DEFAULT '{}',
  retention_until timestamptz NOT NULL, created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE TABLE consent_texts (id uuid PRIMARY KEY, kind text CHECK (kind IN ('processing','attestation')),
  version text, locale text, body text NOT NULL, body_sha256 text NOT NULL, effective_from timestamptz,
  retired_at timestamptz, UNIQUE (kind, version, locale));   -- immutable; real-user use requires HC-7
CREATE TABLE consents (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions,  -- kept as tombstone on erase
  party text CHECK (party IN ('customer','agent')),          -- 'agent' reserved for Later-L1/L2
  method text, consent_text_id uuid REFERENCES consent_texts, consent_text_version text, consent_text_sha256 text,
  locale text, granted_at timestamptz NOT NULL, revoked_at timestamptz, actor_id uuid, client_meta jsonb,
  attestation jsonb,   -- CUSTOMER SELF-DECLARATION, not proof of agent consent:
                       -- {i_was_party, agent_informed, recorded_on?, recording_source?}
  attestation_text_id uuid REFERENCES consent_texts, attestation_text_sha256 text, attested_at timestamptz);
CREATE UNIQUE INDEX consents_one_active_customer ON consents (session_id) WHERE party = 'customer' AND revoked_at IS NULL;   -- v0.2.2 impl sync
CREATE TABLE idempotency_keys (user_id uuid REFERENCES users, key text, method text, path text, request_hash text,
  status_code int, response_body jsonb, created_at timestamptz DEFAULT now(), PRIMARY KEY (user_id, key));   -- 24 h TTL purge

-- audio & transcript
CREATE TABLE audio_streams (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  source text DEFAULT 'upload', original_filename text, detected_format text, sha256 text NOT NULL,
  size_bytes bigint, duration_ms int, channel_layout text CHECK (channel_layout IN ('mono','stereo_split')),
  status text, status_code text, object_key_original text, object_key_normalized text,
  created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE UNIQUE INDEX ON audio_streams (session_id, sha256) WHERE deleted_at IS NULL;
CREATE TABLE transcript_revisions (session_id uuid REFERENCES sessions ON DELETE CASCADE, revision int,
  audio_stream_id uuid REFERENCES audio_streams, provider text, provider_config jsonb, is_current bool,
  created_at timestamptz DEFAULT now(), PRIMARY KEY (session_id, revision));
CREATE TABLE transcript_segments (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int NOT NULL, channel text CHECK (channel IN ('mono','L','R')), speaker_label text,
  text text NOT NULL, start_ms int, end_ms int, language text, asr_avg_conf real, asr_min_numeric_conf real);
CREATE INDEX ON transcript_segments (session_id, transcript_revision, start_ms);
CREATE TABLE transcript_words (segment_id uuid REFERENCES transcript_segments ON DELETE CASCADE, idx int,
  text text, start_ms int, end_ms int, confidence real, speaker_label text, is_numeric bool, PRIMARY KEY (segment_id, idx));
CREATE TABLE speaker_assignments (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int, speaker_label text, segment_id uuid NULL,      -- NULL = whole label; set = per-segment override
  speaker_role text CHECK (speaker_role IN ('agent','customer','unknown')),
  speaker_source text CHECK (speaker_source IN ('diarized','channel','user','inferred')),
  role_assignment text CHECK (role_assignment IN ('auto','user_confirmed')), role_confidence real,
  confirmed_by uuid, confirmed_at timestamptz, is_current bool DEFAULT true);

-- claims
CREATE TABLE claims (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  transcript_revision int NOT NULL, claim_type text, utterance_kind text, speaker_label text,
  normalized_statement text, quote text NOT NULL, quote_anchors jsonb NOT NULL, also_said_at jsonb DEFAULT '[]',
  start_ms int, end_ms int, conditions jsonb, vagueness text, extraction_confidence real,
  asr_avg_conf real, asr_min_numeric_conf real, quote_confirmed_by_user bool DEFAULT false,
  readiness text, missing_inputs text[], verifiable bool, state text DEFAULT 'active',
  supersedes_claim_id uuid, prompt_version text, created_at timestamptz DEFAULT now(), updated_at timestamptz);
  -- speaker_role/speaker_source/role_assignment are resolved from speaker_assignments at read time
CREATE TABLE claim_values (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  field text, raw_text text, quantity text, value numeric, value_low numeric, value_high numeric, comparator text,
  period text, rate_basis text, rate_type text, of_what text, includes_gst bool, normaliser_version text);

-- documents
CREATE TABLE documents (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  kind text, source text CHECK (source IN ('pdf','photos')), version int DEFAULT 1, content_sha256 text NOT NULL,
  status text, status_code text, page_count int, warnings jsonb DEFAULT '[]', created_at timestamptz DEFAULT now(), deleted_at timestamptz);
CREATE UNIQUE INDEX ON documents (session_id, content_sha256) WHERE deleted_at IS NULL;
CREATE TABLE document_pages (document_id uuid REFERENCES documents ON DELETE CASCADE, version int, page int,
  printed_page text, image_key text, width real, height real, text_source text, ocr_conf real, quality jsonb,
  PRIMARY KEY (document_id, version, page));
CREATE TABLE clauses (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  page_start int, page_end int, bboxes jsonb, section_path text, label text, text text NOT NULL, is_table_row bool,
  text_source text, ocr_conf real, ocr_min_numeric_conf real, ord int);
CREATE TABLE doc_terms (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  clause_id uuid REFERENCES clauses ON DELETE CASCADE, field text, value_raw text, value numeric, unit text,
  basis text, rate_type text, conditions jsonb, includes_gst bool, normaliser_version text);
CREATE TABLE chunks (id uuid PRIMARY KEY, document_id uuid REFERENCES documents ON DELETE CASCADE, version int,
  clause_id uuid REFERENCES clauses ON DELETE CASCADE, page int, section_path text, text text,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(section_path,'')||' '||text)) STORED,
  embedding vector(:EMBEDDING_DIM), embedding_model text);          -- dim from ADR-005 (migration parameter)
CREATE INDEX ON chunks USING gin (tsv);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

-- verification
CREATE TABLE verification_runs (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  trigger text, thresholds_version text, features_version text, created_at timestamptz DEFAULT now());
CREATE TABLE verification_results (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  run_id uuid REFERENCES verification_runs ON DELETE CASCADE, version int, is_current bool,
  status text CHECK (status IN ('CONFLICT','MATCH','UNCLEAR','NOT_VERIFIED')), reason_code text, finding_kind text,
  headline text, reason text, severity text, primary_engine text, engine_verdicts jsonb,
  suggested_question text, changed_because jsonb NOT NULL, created_at timestamptz DEFAULT now(),
  UNIQUE (claim_id, version));
CREATE TABLE evidence_links (id uuid PRIMARY KEY, result_id uuid REFERENCES verification_results ON DELETE CASCADE,
  kind text CHECK (kind IN ('spoken','document','calculation','rule','public')),
  quote text, quote_anchors jsonb, start_ms int, end_ms int, speaker_role text, role_assignment text,
  document_id uuid, document_version int, page int, clause_id uuid, clause_text text, bboxes jsonb, ocr_conf real,
  calculation_id uuid, rule_id text, rule_version int, source_url text, payload jsonb);
CREATE TABLE shadow_verdicts (id uuid PRIMARY KEY, claim_id uuid REFERENCES claims ON DELETE CASCADE,
  engine text, rule_id text, verdict jsonb, created_at timestamptz DEFAULT now());   -- never user-visible
CREATE TABLE calculations (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  formula_id text, formula_version int, inputs jsonb, outputs jsonb, assumptions text[], missing text[],
  tolerance_row text, enabled bool, created_at timestamptz DEFAULT now());

-- rules, public data (MVP-limited)
CREATE TABLE rules (id text, version int, body jsonb, source_url text, effective_from date, effective_to date,
  reviewed_by text, hc6_approved_at timestamptz, enabled bool DEFAULT false, PRIMARY KEY (id, version));
CREATE TABLE public_entities (id uuid PRIMARY KEY, name text, normalized_name text, aliases text[], category text,
  registration_no text, regulator text, source_url text, source_date date, demo_only bool DEFAULT true);
CREATE INDEX ON public_entities USING gin (normalized_name gin_trgm_ops);
CREATE TABLE public_snapshots (id uuid PRIMARY KEY, entity_id uuid REFERENCES public_entities, product_type text,
  field text, value_low numeric, value_high numeric, unit text, conditions text, source_url text, captured_at date, captured_by text);

-- Q&A, reports, ops
CREATE TABLE qa_messages (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  question text, route text, answer jsonb, calculation_id uuid, status text, created_at timestamptz DEFAULT now());
CREATE TABLE reports (id uuid PRIMARY KEY, session_id uuid REFERENCES sessions ON DELETE CASCADE,
  status text, object_key text, result_versions jsonb, created_at timestamptz DEFAULT now());
CREATE TABLE events (seq bigserial PRIMARY KEY, session_id uuid NOT NULL, type text, payload jsonb, ts timestamptz DEFAULT now());
CREATE INDEX ON events (session_id, seq);
CREATE TABLE jobs (id uuid PRIMARY KEY, session_id uuid, kind text, dedupe_key text, payload jsonb,
  status text CHECK (status IN ('queued','running','succeeded','failed','cancelled')), attempts int DEFAULT 0,
  error_class text, run_after timestamptz DEFAULT now(), locked_by text, locked_at timestamptz, last_error text,
  cancel_requested_at timestamptz, lease_expires_at timestamptz, finished_at timestamptz,
  created_at timestamptz DEFAULT now());   -- cancellation: doc 01 §5.4; lease: §2 (A8)
CREATE UNIQUE INDEX ON jobs (dedupe_key) WHERE status = 'queued';   -- coalescing (A7)
CREATE INDEX ON jobs (session_id);
CREATE INDEX ON jobs (status, run_after);
CREATE TABLE audit_log (id bigserial PRIMARY KEY, actor_id uuid, action text, target_type text, target_id uuid,
  meta jsonb, ip inet, ts timestamptz DEFAULT now());
-- append-only, enforced by BEFORE UPDATE OR DELETE (row) and BEFORE TRUNCATE (statement) triggers that raise (A13). REVOKE alone does not bind the table owner, which the app connects as
CREATE TABLE deletion_receipts (id uuid PRIMARY KEY, session_id uuid, owner_id uuid, requested_at timestamptz,
  completed_at timestamptz, objects_deleted int, rows_deleted jsonb);
```

**Ownership:** every repository function takes `owner_id` and filters through `sessions.owner_id`. Optional Postgres RLS (Phase 4). There is no sharing between users in the MVP.

**Erasure** (doc 12 §B.5; implemented in Phase 4, T4.3): the `sessions` row is **kept** as the tombstone, so `ON DELETE CASCADE` from `sessions` never fires (A1). `session.erase` therefore deletes explicitly, in one transaction, every session-scoped row **except** the tombstone tables. Tables keyed by `session_id`: `audio_streams`, `transcript_revisions`, `transcript_segments`, `speaker_assignments`, `claims`, `documents`, `verification_runs`, `calculations`, `qa_messages`, `reports`, `events`, `jobs` (non-running), `ws_tickets`. Their children cascade. It then clears `sessions` content columns (`lender_name_raw`, `facts`, `product_type`, `language_hint`) and deletes the object prefixes. It **keeps** a tombstone: `sessions` (id, owner_id, created_at, deleted_at, state='deleted'), `consents` (including attestation and text hashes), `consent_texts`, `audit_log` and `deletion_receipts`. The v0.1 test "no rows remain" is replaced by "only tombstone rows remain".

## 4. REST endpoints
| Method | Path | Phase | Scope |
|---|---|---|---|
| POST | /auth/register, /auth/login, /auth/refresh, /auth/logout | 0 | MVP |
| POST | /auth/ws-ticket `{session_id, purpose:"events"}` | 0 | MVP (purpose `audio` → Later-L1) |
| POST / GET | /sessions ; /sessions/{id} ; GET /sessions/{id}/state | 0 | MVP |
| DELETE | /sessions/{id} (→ 202, erase job, receipt) | 4 (T4.3) | MVP (A14) |
| GET | /consent-texts?kind=&locale= (current versions) | 0 | MVP |
| POST / DELETE | /sessions/{id}/consents (consent + attestation) ; /sessions/{id}/consents/customer (revoke → cancel jobs) | 0 | MVP |
| POST | /sessions/{id}/recordings ; GET/PUT /recordings/{id} | 1 | MVP |
| GET | /sessions/{id}/audio?from_ms&to_ms | 1 | MVP |
| GET | /sessions/{id}/transcript?revision= | 1 | MVP |
| GET / PUT | /sessions/{id}/speakers (mapping; PUT sets `user_confirmed`) | 1 | MVP |
| PATCH | /segments/{id}/speaker | 1 | MVP |
| GET / PATCH | /sessions/{id}/claims ; /claims/{id} | 1 | MVP |
| POST | /claims/{id}/confirm-quote | 1 | Proposed (HC-3) |
| POST / GET / PUT | /sessions/{id}/documents ; /documents/{id} ; /documents/{id}/pages/{n} | 2 | MVP |
| GET | /documents/{id}/pages/{n}/image ; /documents/{id}/clauses | 2 | MVP |
| GET | /sessions/{id}/results ; /results/{id}/history ; POST /sessions/{id}/reverify | 2–3 | MVP |
| GET | /calculations/{id} | 3 | MVP |
| POST / GET | /sessions/{id}/reports ; /reports/{id} | 3 | MVP |
| POST / GET | /sessions/{id}/qa | 3 | MVP-limited |
| GET | /lenders/search ; PUT /sessions/{id}/facts | 3 | MVP-limited (demo) |
| GET | /admin/rules (admin only) | 3 | Shadow |
| GET | /healthz, /readyz | 0 | MVP |

## 4a. Request/response models for Phase 0 endpoints (v0.2.2, A5)
| Endpoint | Request | Response |
|---|---|---|
| `POST /auth/register` | `{email, password (10–256 chars)}` | `201 {user_id, email}`; `409 email_taken` |
| `POST /auth/login` | `{email, password}` | `200 {access_token, token_type:"bearer", expires_in}` + `Set-Cookie: ss_refresh` (httpOnly, Secure except local dev, SameSite=Strict, Path=/api/v1/auth); `401 unauthorized` |
| `POST /auth/refresh` | refresh cookie | `200` same as login, with the cookie **rotated**. A reused revoked token revokes its family and returns `401` |
| `POST /auth/logout` | refresh cookie | `204`, revokes the token and clears the cookie |
| `POST /auth/ws-ticket` | `{session_id, purpose:"events"}` | `200 {ticket, expires_at}` (session must be owned → else `404`) |
| `GET /consent-texts?kind=&locale=` | — | `200 [{id, kind, version, locale, body, body_sha256}]` (current = not retired, latest `effective_from` ≤ now) |
| `POST /sessions` (Idempotency-Key) | `{mode, product_type, lender_name?, language_hint}` | `201 Session` |
| `GET /sessions` | `?cursor&limit` | `200 {items:[Session], next_cursor}` (own only) |
| `GET /sessions/{id}` | — | `200 Session` |
| `GET /sessions/{id}/state` | — | `200 {session: Session, consent: ConsentStatus, last_event_seq}` (later phases add fields) |
| `POST /sessions/{id}/consents` (Idempotency-Key) | `{consent_text_id, accepted:true, attestation:{attestation_text_id, i_was_party:true, agent_informed:"yes"|"no"|"unsure", recorded_on?, recording_source?}}`. **Consent and attestation are submitted together in one request** (A15) | `201 ConsentStatus`; `409 invalid_state` if the session is not `consent_pending` or consent is already active; `422 validation_error` if a text id is retired, of the wrong kind, `accepted≠true` or `i_was_party≠true` |
| `DELETE /sessions/{id}/consents/customer` | — | `200 {session: Session, jobs_cancelled, jobs_cancel_requested}` |
| `GET /healthz` | — | `200 {status:"ok"}` (process liveness only) |
| `GET /readyz` | — | `200 {db, storage, listener: "ok"}` or `503` listing failures |

- `Session = {id, mode, state, state_reason, product_type, language_hint, lender_name, retention_until, created_at}`.
- `ConsentStatus = {active: bool, consent_id, consent_text_version, granted_at, revoked_at, attestation: {i_was_party, agent_informed, recorded_on, recording_source, attested_at}, attestation_note}`.
- `attestation_note` is the canonical customer-statement wording from doc 01 §5.3 and is never phrased as agent consent.

## 5. Events WS (canonical type list)
`/ws/sessions/{id}/events?ticket=&since=`. Envelope `{type, session_id, seq, ts, payload}`.

**Semantics (A9, A10):**
- Auth uses a single-use ticket bound to user + session + `purpose=events`. Consent is **not** required.
- On connect, the server replays `events.seq > since` for that session (ascending), then streams live events.
- `seq` is global and monotonic. Clients drop `seq ≤ last_seen`.
- Heartbeat: server `{"type":"ping"}` every 20 s; client may send `{"type":"pong"}`.
- Phase 0 producers: `session.status` (on consent → `ready`, on revocation → `failed`).

| type | payload | Scope |
|---|---|---|
| `session.status` | `{state, reason?}` | MVP |
| `recording.status` | `{recording_id, stage, progress, code?}` | MVP |
| `transcript.ready` | `{transcript_revision, segment_count}` | MVP |
| `speaker.updated` | `{transcript_revision, mapping[], role_assignment}` | MVP |
| `claim.created` / `claim.updated` | Claim (doc 04) | MVP |
| `document.status` | `{document_id, version, stage, progress, code?, warnings[]}` | MVP |
| `verification.updated` | VerificationResult (doc 08) | MVP |
| `qa.answer` | `{qa_id, status, answer?}` | MVP-limited |
| `report.status` | `{report_id, status}` | MVP |
| `error` | `{code, message, retryable, job_kind?}` | MVP |
| `transcript.partial`, `transcript.committed` | Live | Later-L1 |

## 6. Storage layout
- `audio/{session}/{stream}/original`, `…/normalized.wav|flac`, `…/L.wav|R.wav`
- `documents/{doc}/v{version}/original/*`, `…/pages/{n}.webp`, `…/docling.json`
- `exports/{session}/{report}.pdf`

All buckets are private with SSE. Presigned GET URLs last 5 min.

## 7. Testing & acceptance
- OpenAPI snapshot. JSON Schemas generated from Pydantic and shared with the frontend.
- Migration up/down.
- Idempotency suite: replay, 409, content dedupe, job dedupe, handler re-run equality.
- Erase test: only tombstone rows remain and object prefixes are empty.
- Column/model parity test: each Pydantic field in docs 04/08 maps to a column or a documented derived field.
