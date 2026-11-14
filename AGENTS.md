# AGENTS.md — SaidSigned

Guide for AI coding agents (Claude Code, Codex, etc.) working in this repository.

## What this project is
SaidSigned checks a recorded loan-sales call against the loan agreement the customer was given. It flags anything the
agent promised that the signed terms contradict, citing the exact quote, timestamp and clause.
Upload-first MVP: the customer uploads a call recording and a PDF or photos of the agreement.

## Sources of truth
| What | Where |
|---|---|
| Tasks, dependencies, checkpoints | `docs/revision/MVP_IMPLEMENTATION_PLAN.md` |
| Task status (evidence-based) | `docs/revision/MVP_TASK_STATUS.md` (authoritative if it differs from this file) |
| Architecture, per module | `docs/01`–`docs/14`; master report `docs/SAIDSIGNED_MASTER_ARCHITECTURE_REPORT.md` |
| Decisions and open questions | `docs/14_ARCHITECTURE_DECISIONS_AND_OPEN_QUESTIONS.md` |
| Canonical owners (ADR-018) | doc 08: statuses, reason codes, evidence, thresholds · doc 06: tolerances · doc 01: retry policy, error codes · doc 11: idempotency, events, DDL · doc 03 §5: speaker model |

## Rules that must not be broken
1. **Never assume or bypass a human checkpoint (HC-1…HC-8).** Stop before a gated task, present the decision with a
   recommendation, and wait for explicit approval. Never record an approval that was not given.
2. **Do not start a new stage** until the current stage is complete and its exit checkpoint is approved.
3. **Mark a task done only with evidence** (tests, a real run). Update `MVP_TASK_STATUS.md` when you do.
4. **Keep MVP scope.** Deferred features (Later-L1…L9) stay deferred. No features just to score marks.
5. **Finance figures stay hidden until HC-5**; regulatory rules stay in shadow mode until HC-6 approves each rule.
6. **No real customer data until HC-7 and HC-8.** Use the synthetic agreements in `eval/datasets/` and team-recorded calls.
7. **Wording:** never say an agent is lying, fraudulent or acting illegally. The customer's attestation is the
   customer's own statement, never "agent consent". Lint tests enforce this.
8. **The LLM never supplies numeric values**; the deterministic normaliser does (doc 04 §6).
9. Never commit `.env` or secrets. Commit or push only when the user asks.

## Commands
```sh
make env        # create .env with random local secrets (won't overwrite)
make up         # full Docker stack: db, minio (built from source, Q-16), api :8000, worker
make db         # Postgres only
make migrate    # alembic upgrade + seed DRAFT consent texts
make ci         # everything CI runs: ruff, mypy, pytest (REQUIRE_DB=1), frontend lint/typecheck/vitest/build, dataset check
cd backend && uv run pytest -q                 # backend tests (needs `make db` and ffmpeg)
cd frontend && npm run dev                     # UI on :5173, proxies /api and /ws to :8000
```
`make ci` fails (rather than skipping) if Postgres or ffmpeg is missing.

## Layout
```
backend/app/core/        config, db, errors (doc 01 §7), retry (doc 01 §6), idempotency, storage, security
backend/app/modules/     auth, sessions, consent, audit, realtime (events/WS), audio (T1.1), stt (ElevenLabs, T1.2), speakers (T1.3), claims (normaliser T1.5, extraction T1.6), documents (T2.1–T2.4), rag (retrieval), verify (orchestrator T2.5), contract (T2.6), finance (tolerances)
backend/app/llm/         LLM provider interface, Bedrock Mantle provider (Gemma), fakes
backend/app/schemas/     canonical doc 08 / doc 04 schemas, reason codes, CONFLICT validator, wording (T1.4)
backend/app/worker/      Postgres job queue, runner, handlers.register_all
backend/alembic/         migrations 0001 foundation … 0011 reports_qa
frontend/src/            React PWA: features/{auth,sessions,consent,recordings,speakers,claims,transcript,documents,results}, lib (api, events), i18n en/hi
eval/                    synthetic datasets (agreements AG01–AG10), tools, reports
infra/                   docker-compose, MinIO Dockerfile
```

## Stages (MVP = Stages 0–4, 40 tasks)
Status as of 2026-11-14. Legend: ✅ done · ⬜ not started · ⛔ blocked (needs approval or input).

**Overall: 30 / 40 tasks done (75%).** Stage 0 complete; Stage 1 7/8 (T1.8 partial); Stage 2 complete (7/7), HC-4 approved; Stage 3 in progress.

### Stage 0 — Foundation · 13 tasks · ✅ COMPLETE (13/13)
Gate: HC-1 ✅ approved 2026-11-13.

| Task | What | Status |
|---|---|---|
| T0.1 | Repo layout, Docker stack (pgvector, MinIO), settings, logging | ✅ |
| T0.2 | Migration 0001 (core tables) | ✅ |
| T0.3 | Error model + retry classifier | ✅ |
| T0.4 | Auth (register/login/refresh) + WebSocket tickets | ✅ |
| T0.5 | Sessions, consent + attestation, revocation with job cancellation, consent gate, audit | ✅ |
| T0.6 | Idempotency middleware | ✅ |
| T0.7 | Job queue: dedupe, leases, retries, cancellation | ✅ |
| T0.8 | Event bus + WebSocket resume | ✅ |
| T0.9 | Audit service (append-only) | ✅ |
| T0.10 | STT / LLM provider interfaces + fakes | ✅ |
| T0.11 | Frontend shell (not yet tested in a real browser; E2E with T1.7) | ✅ |
| T0.12 | CI (feature-flag startup checks, DB required) | ✅ |
| T0.13 | 10 synthetic agreements + DRAFT consent texts (legal review at HC-7) | ✅ |

### Stage 1 — Upload, speech-to-text, speakers, claims · 8 tasks · IN PROGRESS (7/8)
Gates: HC-2 ✅ approved 2026-11-14 (conditions: plan §4) · HC-3 at stage exit.

| Task | What | Status | Blocked by |
|---|---|---|---|
| T1.1 | Recording upload: magic-byte check, dedupe, normalise, replace, snippets | ✅ | — |
| T1.2 | ElevenLabs batch STT (diarization, word timestamps) + transcript revisions | ✅ | — |
| T1.3 | Speaker suggestion (Gemma) + one-tap agent confirmation | ✅ | — |
| T1.4 | Schema registry (statuses, reason codes, evidence, claims, wording) | ✅ | — |
| T1.5 | Deterministic normaliser (422-case table, Hinglish, Devanagari) | ✅ | — |
| T1.6 | Claim extraction (Gemma) with verbatim quote anchors; few-shots approved | ✅ | — |
| T1.7 | Claims UI, transcript view, snippet playback | ✅ | — |
| T1.8 | Experiments E2/E3/E10 + LLM evaluation | ◐ | Reports done (synthetic TTS); E10 5-user usability test needs people |

### Stage 2 — Documents and contract verification · 7 tasks · ✅ COMPLETE (7/7)
Gate: HC-4 ✅ approved 2026-11-14.

| Task | What | Status |
|---|---|---|
| T2.1 | Document upload (PDF + photos), sanitise, versioning | ✅ |
| T2.2 | Docling/OCR + confidence pass (E5: 190/190 on simulated scans/photos) | ✅ |
| T2.3 | Clause segmentation + doc_terms (boundary F1 1.000 digital / 0.926 photos) | ✅ |
| T2.4 | Chunks, embeddings (E5-small, provisional ADR-005), hybrid retrieval (E6 R@5 0.993) | ✅ |
| T2.5 | Orchestrator v1 (gates, merge, validators, versioned results, triggers) | ✅ |
| T2.6 | Contract engine (said vs signed; 200 pairs, CONFLICT precision 1.000) | ✅ |
| T2.7 | Results dashboard + evidence drawer + document upload screen (real-stack E2E demo) | ✅ |

### Stage 3 — Finance, orchestration, reporting · 7 tasks · IN PROGRESS (3/7)
Gate: HC-5 at stage exit (finance stays hidden until then). Produces the HC-6 package.

| Task | What | Status |
|---|---|---|
| T3.1 | Finance engine: SourcedFactory, MVP formulas, traces (golden 80/80; hidden until HC-5) | ✅ |
| T3.2 | Merge v2 (C-CALC-DOC, internal_inconsistency; finance hidden until HC-5) | ✅ |
| T3.3 | Results UI v2 + PDF report (snapshot + wording tests) | ✅ |
| T3.4 | Ask-lite with routing + citations | ⬜ |
| T3.5 | Shadow rules + demo lenders | ⬜ |
| T3.6 | Gated formulas (off for the whole MVP) | ⬜ |
| T3.7 | HC-6 rule review package | ⬜ |

### Stage 4 — Evaluation, security, release · 5 tasks · NOT STARTED (0/5)
Gates: HC-6 (per rule), HC-7 (legal, before real users), HC-8 (release).

| Task | What | Status |
|---|---|---|
| T4.1 | Full evaluation + threshold calibration | ⬜ |
| T4.2 | Security controls + tests | ⬜ |
| T4.3 | Retention, erase, audit completeness | ⬜ |
| T4.4 | Accessibility, Hindi copy, UX test | ⬜ |
| T4.5 | Staging deploy + load test | ⬜ |

### After the MVP (deferred, not started)
Later-L1 live mic · L2 Google Meet capture · L3 Meet API spike · L4 advanced RAG · L5 automated lender data ·
L6 expanded regulatory/finance · L7 insurance · L8 second STT provider · L9 lender dashboards.

## Providers (configured in `.env`, smoke-tested 2026-11-14)
| Purpose | Provider | Setting |
|---|---|---|
| Speech-to-text | ElevenLabs Scribe v2 (`scribe_v2`), batch with diarization | `ELEVENLABS_API_KEY` |
| LLM | Gemma 4 31B (`google.gemma-4-31b`) on Amazon Bedrock Mantle, OpenAI-compatible Chat Completions at `/openai/v1` | `MANTLE_*` |

Speech-to-text is live (T1.2, job `stt.batch`). The LLM is live for speaker-role suggestion (T1.3, `speakers.suggest`) and claim extraction (T1.6, `claims.extract`), with PII redacted first. ADR-004 decided 2026-11-14: Gemma 4 31B on Amazon Bedrock (`google.gemma-4-31b`).

## Human checkpoints
| ID | Approves | Status |
|---|---|---|
| HC-1 | Docs + plan | ✅ Approved 2026-11-13 |
| HC-2 | Claim schema, reason codes, evidence rules, wording | ✅ Approved 2026-11-14 (English wording; Hindi later) |
| HC-3 | STT/diarization results, languages | ✅ Approved 2026-11-14 (quote confirmation for numeric CONFLICTs; EN Supported, Hinglish Beta, Hindi Not yet supported; ElevenLabs only) |
| HC-4 | OCR, retrieval, embeddings | ✅ Approved 2026-11-14 (Docling + RapidOCR, e5-small, hybrid retrieval, thresholds provisional) |
| HC-5 | Finance formulas and visibility | ⬜ Pending (Stage 3 exit) |
| HC-6 | Each regulatory rule | ⬜ Pending (Stage 4) |
| HC-7 | Legal: consent texts, privacy, retention | ⬜ Pending (before real users) |
| HC-8 | Release readiness | ⬜ Pending (Stage 4 exit) |
