# 01 — Project Setup & Foundation (Phase 0)

> **Version 0.2.2 — 2026-11-13 (0.2.1: §5 consent clarified; 0.2.2: implementation audit fixes A1–A18, HC-1 approved).** Original: `_archive/v0.1-2026-11-13/01_…md`.
> **Scope:**
> - [MVP] skeleton, auth, sessions, consent and recording attestation, events WS (progress/results), job queue, **canonical retry policy**, **canonical API error model**, audit, CI.
> - [Deferred → Later-L1] audio WS tickets and agent live-consent read-out.
>
> **Canonical here:** retry policy (§6), API error codes (§7), consent model (§5). Idempotency is canonical in doc 11 §2. Statuses and reason codes are canonical in doc 08.

## 1. Purpose
Build the base every later phase relies on:
- Repository layout and local infra (Postgres 16 + pgvector, MinIO)
- FastAPI app factory, configuration, logging
- Auth, sessions, consent
- Event bus
- Job queue and worker
- Provider protocols (`LLMProvider`, `EmbeddingProvider`, `STTProvider`)
- CI

No AI features are built in this phase.

## 2. Prerequisites
- **HC-1 APPROVED** by the user (project owner) on 2026-11-13, per the instruction to proceed to implementation. HC-2…HC-8 remain **pending**.
- Tooling: Python 3.12, `uv`, Node ≥ 20 with **npm** (pnpm optional; v0.2.2 change A17), Docker (Compose v2).

## 3. Structure
```
backend/app/
  main.py  core/{config,db,storage,logging,errors,security,retry}.py
  llm/base.py                         # protocols (docs 04, 09)
  modules/{auth,sessions,consent,audit,realtime}/
  worker/{runner.py, registry.py}
backend/alembic/versions/0001_foundation.py
backend/config/{thresholds.yaml, features.yaml}   # thresholds per doc 08 §5; feature flags (APR etc.)
infra/docker-compose.yml   frontend/   .github/workflows/ci.yml
```

## 4. Implementation steps
1. **Compose:**
   - `pgvector/pgvector:pg16`
   - `minio/minio`, with buckets `audio`, `documents`, `exports` created by an init container
   - `api` (uvicorn)
   - `worker`, from the same image
2. **Settings** (pydantic-settings, env only; secrets never logged):
   - Connections: `DATABASE_URL`, `S3_*`
   - Auth: `JWT_SECRET`, `JWT_ACCESS_TTL=900`, `JWT_REFRESH_TTL=1209600`, `WS_TICKET_TTL=30`
   - Providers: `ELEVENLABS_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`
   - Limits: `RETENTION_DAYS=30` [Q-11], `MAX_UPLOAD_MB_AUDIO=200`, `MAX_AUDIO_MINUTES=180`, `MAX_UPLOAD_MB_DOC=40`, `MAX_DOC_PAGES=60`, `MAX_PHOTO_PAGES=20`
   - Config files: `THRESHOLDS_FILE`, `FEATURES_FILE`
3. **Migration 0001:** create the tables listed in doc 11 §3 for foundation: `users`, `refresh_tokens`, `ws_tickets`, `sessions`, `consent_texts`, `consents`, `idempotency_keys`, `jobs`, `events`, `audit_log`, `deletion_receipts`.
4. **Auth:**
   - `POST /api/v1/auth/register|login|refresh|logout`
   - `POST /api/v1/auth/ws-ticket {session_id, purpose:"events"}` returns a single-use ticket with a 30 s TTL. Purpose `audio` is deferred to Later-L1.
5. **Sessions:**
   - `POST /api/v1/sessions {mode:"upload", product_type, lender_name?, language_hint}`. Modes `in_person` and `meet_tab` are deferred and return `422 mode_not_available` in the MVP. Request/response models are in doc 11 §4a.
   - `product_type`: `personal_loan | home_loan | vehicle_loan | business_loan | gold_loan | other_loan` (restored from v0.1; A5). `language_hint`: `en | hi | hinglish`.
   - States: `consent_pending → ready → processing → completed | failed | deleted`. A new session is persisted directly as `consent_pending` (v0.2.1's transient `created` state is dropped; A6).
   - Transitions in Phase 0: `consent_pending → ready` when an active customer consent **and** attestation exist; `consent_pending | ready → failed (state_reason=consent_revoked)` on revocation. `failed` is terminal: a new session is needed. `processing`, `completed` and `deleted` are set by later phases.
6. **Consent and recording attestation (§5).**
7. **Event bus:** insert into `events` (gets a `seq`), then `NOTIFY session_events '<session_id>:<seq>'` **after commit**. The API listener on a dedicated asyncpg connection fans the event out to `/ws/sessions/{id}/events`. Clients resume with `?since=`.
8. **Job queue** (semantics canonical in doc 11 §2; A7, A8):
   - Claim with `SELECT … FOR UPDATE SKIP LOCKED`. A job is skipped while another job with the same `dedupe_key` is `running`.
   - Dedupe **coalesces**: at most one `queued` and at most one `running` job per key. A trigger that arrives while a job is running enqueues exactly one follow-up.
   - **Lease:** claiming sets `lease_expires_at = now() + JOB_LEASE_SECONDS` (default 600). Long handlers renew it with heartbeats. A `running` job whose lease expired is reclaimed as a `transient` failure attempt.
   - Semantics: at-least-once execution; handlers must be idempotent.
   - Retries follow §6.
9. **Audit:** `audit.record(actor_id, action, target_type, target_id, meta)` for the actions listed in doc 12 §B.4.
10. **Frontend skeleton:**
    - Routes: `/login`, `/sessions`, `/sessions/new`, `/sessions/:id` (upload-first flow, doc 10)
    - `api.ts` with refresh and Idempotency-Key generation for POSTs
    - `events.ts` WS client with resume
    - PWA shell, with no caching of `/api` or media
11. **CI:** ruff, mypy (strict for `engines/finance` and `orchestrator`), pytest with Postgres, eslint, tsc, vitest.

## 5. Consent model & recording attestation [MVP] (canonical; revised v0.2.1)

**What these records are, and are not:**
- Records **the customer's own consent** to SaidSigned processing their recording and documents.
- Records **the customer's own declarations** about the recording (the *attestation*).
- The attestation is a self-declaration by the uploader. It is **not independent proof** that the agent consented to, or was informed of, the recording. SaidSigned does not verify it.
- UI, reports and exports must phrase it as *"The customer states that …"*, plus the note *"(not independently verified)"*.
- No doc, UI string or report may describe it as agent consent.

### 5.1 Versioned consent text
- `consent_texts(id, kind: processing|attestation, version, locale, body, body_sha256, effective_from, retired_at)`. Versions are immutable. A change creates a new version.
- **Approval:** text content is drafted in Phase 0. Using it with real users requires **HC-7**.
- The UI fetches the current version for the user's locale (en/hi) and displays it in full before the checkbox.

### 5.2 Customer consent record
`POST /api/v1/sessions/{id}/consents` (Idempotency-Key) with `{consent_text_id, accepted: true}`.

The server stores:

| Field | Value |
|---|---|
| `party` | `customer` |
| `method` | `in_app_checkbox` |
| `consent_text_id` | Text identifier |
| `consent_text_version` | Text version |
| `consent_text_sha256` | Hash of the exact text shown |
| `locale` | Display locale |
| `granted_at` | Server UTC timestamp |
| `actor_id` | User ID |
| `client_meta` | User-agent hash only; no raw IP stored outside `audit_log` |

### 5.3 Recording attestation (customer self-declaration)
- Submitted with its own versioned text (`kind=attestation`) and stored in `consents.attestation` with `attested_at`, `attestation_text_id` and `attestation_text_sha256`.
- Required before `POST /recordings`. Fields:
  - `i_was_party: true` (required; uploads declared as not involving the customer are refused)
  - `agent_informed: yes | no | unsure`, exactly as the customer declared it
  - `recorded_on?`
  - `recording_source?: phone_recorder | call_recorder | other`
- **[Q-13, legal — HC-7]** Policy when `agent_informed ≠ yes`. Until HC-7 decides, the MVP:
  - stores the answer
  - shows the warning "Recording someone without their knowledge may have legal implications"
  - marks the report: "The customer states the agent was not / may not have been informed of the recording (not independently verified)"

  When `agent_informed = yes`, the report says: "The customer states the agent was informed of the recording (not independently verified)". No legal position is assumed.

### 5.4 Revocation and processing cancellation
`DELETE /api/v1/sessions/{id}/consents/customer`, in one transaction:
1. Sets `revoked_at`.
2. Sets the session to `failed`, `state_reason=consent_revoked`.
3. Marks all `queued` jobs for the session `cancelled`.
4. Sets `cancel_requested_at` on `running` jobs.

Then:
- Running handlers check `cancel_requested_at` at each step boundary: before provider calls, before each DB write batch, and before publishing events. They stop, roll back the current transaction and end as `cancelled`. Their outputs are not committed.
- After revocation, no new jobs can be enqueued for the session (gate in `enqueue()`).
- The user is offered immediate erasure (doc 12 §B.5).
- Provider calls already in flight cannot be recalled. Their results are discarded. Provider-side retention follows doc 12 §B.5 [A].

### 5.5 Gate
Recording upload, document upload, extraction, verification, Q&A and report generation return `403 consent_required` without an active (non-revoked) customer consent **and** an attestation.

### 5.6 Audit evidence
Every action writes an `audit_log` entry (doc 12 §B.4) with ids, text version and text hash, but no content:
- `consent.grant`
- `attestation.submit`
- `consent.revoke`
- `jobs.cancelled` (count)
- each `job.cancel_observed`

Consent rows, attestation and audit entries survive erasure as the tombstone (doc 11 §3).

### 5.7 Deferred
**[Deferred → Later-L1/L2]** Agent consent records (`party=agent`, verbal read-out, Meet chat notice) and the live recording indicator. The v0.1 design is kept in doc 02 §Deferred. Even then, an in-app "Agent agreed" tap by the customer would remain a customer-entered record, not independent proof.

## 6. Retry policy (canonical, used by all docs)
| Error class | Examples | In-call retry | Job-level retry | Final outcome |
|---|---|---|---|---|
| `transient` | Network, timeout, HTTP 5xx, 429 (honour `Retry-After`) | 2 retries, backoff 1 s → 4 s (+ jitter) | Up to 5 attempts total: 30 s, 2 min, 10 min, 30 min | Job `failed`; affected items get `NOT_VERIFIED(engine_error)` or document `failed`; `error{retryable:true}` event |
| `schema` | LLM output fails Pydantic or validators | 1 corrective retry with the validation errors appended | None | Item dropped and logged (`extraction_failed`); counted in eval metrics; never a guessed value |
| `input` | Unsupported or corrupt file, encrypted PDF, over limits | None | None | 4xx or document `failed` with a code; user-actionable message |
| `internal` | Unexpected exception | None | 2 attempts | Job `failed`, alert; affected items get `NOT_VERIFIED(engine_error)` |

The `core/retry.py` decorator classifies exceptions into these classes. Each job stores `attempts`, `error_class` and `last_error` (redacted). **No error path may produce MATCH** (doc 08 §2).

## 7. API error model (canonical)
Errors use RFC 7807 `application/problem+json`: `{type, title, status, detail, code, request_id}`.

| Code | HTTP |
|---|---|
| `validation_error` | 422 |
| `mode_not_available` | 422 |
| `consent_required` | 403 |
| `forbidden` | 403 |
| `not_found` | 404 |
| `conflict_idempotency_key` | 409 |
| `unsupported_media` | 415 |
| `too_large` | 413 |
| `too_long` | 413 |
| `too_many_pages` | 413 |
| `encrypted_pdf` | 400 |
| `rate_limited` | 429 |
| `provider_unavailable` | 503 |
| `internal_error` | 500 |

Additional codes (v0.2.2, A3):

| Code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing, invalid or expired access token or refresh cookie |
| `idempotency_key_required` | 400 | A create endpoint was called without `Idempotency-Key` |
| `idempotency_in_progress` | 409 | Same key still in flight (`Retry-After: 2`) |
| `invalid_state` | 409 | The action is not allowed in the session's state (e.g. consent on a `failed` session, or a duplicate active consent) |
| `email_taken` | 409 | Registration with an existing email |

- **Ownership:** a resource owned by another user returns `404 not_found`, never 403, to avoid leaking existence (A4).
- WS close codes: `4401` auth (bad, expired or used ticket), `4404` session not found or not owned. `4403` consent applies only to the deferred audio WS. The **events WS does not require consent**, so the UI can show `session.status` before consent (A9).

## 8. Interfaces
```python
class EventEnvelope(BaseModel):
    type: EventType            # canonical list in doc 11 §5
    session_id: UUID; seq: int; ts: datetime; payload: dict

class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None: ...
    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str: ...
    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...
```

## 9. Dependencies
- **Backend:** fastapi, uvicorn, pydantic v2, pydantic-settings, SQLAlchemy 2 (async), asyncpg, alembic, structlog, argon2-cffi, pyjwt, aioboto3, python-multipart, httpx.
- **Frontend:** react 18, react-router, @tanstack/react-query, zustand, tailwindcss, vite-plugin-pwa, i18next.

Nothing is installed until implementation is approved.

## 10. Testing
- Unit:
  - JWT and ticket single-use/expiry
  - consent + attestation gate
  - retry classifier (each class → expected behaviour)
  - error model
- Integration:
  - NOTIFY → WS delivery, and resume with `since`
  - two workers never run the same job
  - the `dedupe_key` blocks duplicate enqueue
  - a revoked consent cancels queued jobs, a running job observes `cancel_requested_at` and commits nothing, and no new jobs can be enqueued
  - consent rows store the text version and hash; the attestation is never rendered as agent consent (UI/report string test)

## 11. Acceptance criteria (Phase 0 exit)
- `docker compose up` starts all services, and `/readyz` reports DB, storage and listener OK.
- A user can register, create an upload session, give consent and attestation, and receive the resulting `session.status{state:"ready"}` event over the events WS within 200 ms of the consent commit (A10).
- The reusable consent-gate dependency returns `403 consent_required` when there is no active consent and attestation. Upload endpoints don't exist until Phase 1, so this is verified against a **test-only route** mounted only in tests. Phase 1 endpoints must use this dependency (A11).
- The retry policy is covered by tests for all four classes.
- CI is green. Until a git remote with GitHub Actions exists, "green" means the workflow's exact commands pass locally via `make ci`, and this is reported as such (A18).

## 12. Human review
**HC-1 APPROVED** by the user (project owner) on 2026-11-13, per the instruction to proceed to implementation. HC-2…HC-8 remain **pending**. HC-1 gated the start of this phase.

## 13. Common failures
- Using a pooled connection for LISTEN.
- Sending NOTIFY before commit, so the client fetches a row that doesn't exist yet.
- Retrying `input` errors.
- A service worker caching API responses.
