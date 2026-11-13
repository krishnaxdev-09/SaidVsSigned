# 01 — Project Setup & Foundation (Roadmap P0)

Labels: [V] verified · [A] assumption · [R] recommendation · [Q] open question. See the master report for sources.

## Purpose
Create the skeleton that every later phase builds on:
- Monorepo layout and local infrastructure (Postgres + pgvector, MinIO).
- FastAPI app factory, configuration, logging and error model.
- Auth with WS tickets.
- Sessions and consent.
- The `realtime` event bus (LISTEN/NOTIFY → WS).
- The Postgres job queue and worker.
- The `LLMProvider` / `EmbeddingProvider` / `STTProvider` protocol stubs.
- CI.

No AI features are built in this phase.

## Prerequisites
- HC-1 approved (architecture, taxonomy, folder layout).
- Tooling: Python 3.12, `uv`, Node 20 LTS + pnpm, Docker Desktop.

## Suggested structure
```
backend/app/
  main.py                    # create_app(): routers, middleware, lifespan (db pool, listener)
  core/config.py             # pydantic-settings Settings
  core/db.py                 # SQLAlchemy 2.0 async engine + session dependency
  core/storage.py            # ObjectStore protocol + S3/MinIO impl (boto3 / aioboto3)
  core/logging.py            # structlog JSON, request_id / session_id contextvars
  core/errors.py             # AppError hierarchy + exception handlers -> problem+json
  core/security.py           # password hashing (argon2), JWT encode/decode, ws tickets
  llm/base.py                # LLMProvider, EmbeddingProvider protocols (doc 04, doc 09)
  modules/auth/{router,service,models,schemas}.py
  modules/sessions/{router,service,models,schemas}.py
  modules/consent/{router,service,models,schemas}.py
  modules/audit/{service,models}.py
  modules/realtime/{events.py,bus.py,ws.py}
  worker/{runner.py, registry.py}
backend/alembic/versions/0001_foundation.py
infra/docker-compose.yml
frontend/ (Vite React TS template + Tailwind + vite-plugin-pwa)
.github/workflows/ci.yml
```

## Implementation steps
1. **Compose.** Use `pgvector/pgvector:pg16` (env `POSTGRES_DB=saidsigned`), `minio/minio` with buckets `audio`, `documents`, `exports` created by an `mc` init container, then `api` (uvicorn) and `worker` (`python -m app.worker.runner`) images built from `backend/`.
2. **Settings.** `Settings` fields:
   - `DATABASE_URL`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`
   - `JWT_SECRET`, `JWT_ACCESS_TTL=900`, `JWT_REFRESH_TTL=1209600`, `WS_TICKET_TTL=30`
   - `ELEVENLABS_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`
   - `RETENTION_DAYS=30`, `MAX_UPLOAD_MB_AUDIO=200`, `MAX_UPLOAD_MB_DOC=40`, `MAX_DOC_PAGES=60`
   - `THRESHOLDS_FILE=config/thresholds.yaml`

   Secrets come only from env and are never logged.
3. **Migration 0001.**
   - Extensions: `vector`, `pgcrypto`.
   - Tables: `users`, `refresh_tokens`, `ws_tickets`, `sessions`, `consents`, `jobs`, `events` (outbox for resumable WS), `audit_log`. Full DDL is in doc 11.
4. **Auth.**
   - `POST /api/v1/auth/register`, `/login`, `/refresh`, `/logout`.
   - `POST /api/v1/auth/ws-ticket` → `{ticket, expires_at}`. The ticket is a random 32-byte, single-use value bound to `user_id` and `session_id`, with a 30 s TTL.
5. **Sessions.**
   - `POST /api/v1/sessions` with `{mode: in_person|meet_tab|upload, lender_name?, product_type: personal_loan|home_loan|vehicle_loan|business_loan|gold_loan|other_loan, language_hint: en|hi|hinglish}`.
   - States: `created → consent_pending → active → processing → completed | aborted | deleted`.
6. **Consent.** `POST /api/v1/sessions/{id}/consents` with `{party: customer|agent, method: in_app_checkbox|verbal_readout|recorded_verbal, consent_text_version, locale}`. Audio WS and uploads must return `403 consent_required` unless a customer consent exists **and** (for live modes) an agent consent or a recorded verbal consent marker exists. `DELETE …/consents/{party}` revokes consent: it closes the audio WS and emits `session.status{recording:false, reason:"consent_revoked"}`.
7. **Event bus.**
   - `realtime.bus.publish(session_id, type, payload)` inserts into `events` (gets `seq` BIGSERIAL), then `NOTIFY session_events, '<session_id>:<seq>'`.
   - The API process runs one `asyncpg` listener connection. It fans out to the in-memory subscribers of `/ws/sessions/{id}/events`, which fetch the row by seq and send it.
   - On connect with `?since=N`, the server replays `events where seq > N`.
8. **Job queue.**
   - `jobs(id, kind, payload jsonb, status, attempts, run_after, locked_by, locked_at, last_error)`.
   - The worker loops with `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1 WHERE status='queued' AND run_after<=now()`.
   - Handlers are registered by `kind`. Retry uses exponential backoff with a maximum of 5 attempts, then `failed`, which emits an `error` event.
9. **Audit.** `audit.record(actor_id, action, target_type, target_id, meta)` is called for consent, upload, export, delete and admin reads.
10. **Error model.** Errors use RFC 7807 `application/problem+json`: `{type, title, status, detail, code, request_id}`. Codes: `consent_required`, `unsupported_media`, `too_large`, `not_found`, `forbidden`, `rate_limited`, `provider_unavailable`, `validation_error`.
11. **Frontend skeleton.**
    - Routes: `/login`, `/sessions/new`, `/sessions/:id/live`, `/sessions/:id/results`, `/sessions/:id/ask`.
    - Code: an `api.ts` fetch wrapper with refresh, and an `events.ts` WS client with resume-by-seq and backoff.
    - PWA manifest and service worker with app-shell caching only; never cache API responses that hold personal data.
12. **CI.** ruff + mypy + pytest (with a Postgres service), plus eslint + tsc + vitest.

## Interfaces
```python
class EventEnvelope(BaseModel):
    type: Literal["transcript.partial","transcript.committed","speaker.updated","claim.created",
                  "claim.updated","document.status","verification.updated","qa.answer",
                  "session.status","error"]
    session_id: UUID
    seq: int
    ts: datetime
    payload: dict

class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, data: bytes | AsyncIterator[bytes], content_type: str) -> None: ...
    async def get_url(self, bucket: str, key: str, ttl_s: int = 300) -> str: ...  # presigned
    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...
```

## Dependencies [R]
- **Backend:** fastapi, uvicorn[standard], pydantic v2, pydantic-settings, sqlalchemy[asyncio] 2, asyncpg, alembic, structlog, argon2-cffi, pyjwt, aioboto3, python-multipart, httpx, websockets.
- **Frontend:** react 18, react-router, @tanstack/react-query, zustand, tailwindcss, vite-plugin-pwa, i18next.

## Error handling
- On startup, fail fast if `vector` is missing or the DB is unreachable.
- On listener disconnect, reconnect with backoff and log `realtime_listener_reconnect`.
- On WS auth failure, close with code `4401`. On consent missing, close with `4403`.

## Testing
- Unit tests: JWT, tickets (single use, expiry), consent gate.
- Integration tests: publish → NOTIFY → WS client receives the envelope; resume with `since`.
- Job queue: two workers never process the same job; retry/backoff.

## Acceptance criteria
- `docker compose up` brings up all services, and `/healthz` reports DB, storage and listener as OK.
- A user can register, create a session, record consent, and open the events WS with a ticket. A test event arrives within 200 ms.
- Audio WS without consent closes with 4403.
- CI is green.

## Human review checkpoint
HC-1 must be approved before starting. No new checkpoint here.

## Common failures
- Holding a pooled connection for LISTEN starves the pool. **Use a dedicated asyncpg connection.**
- Forgetting single-use enforcement on tickets lets a leaked URL be replayed.
- The service worker caches `/api` responses, which leaks data on shared phones.
