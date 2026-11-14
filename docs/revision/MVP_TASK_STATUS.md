# SaidSigned — MVP Task Status Tracker

> Companion to [MVP_IMPLEMENTATION_PLAN.md](MVP_IMPLEMENTATION_PLAN.md), which defines the tasks.
> This file tracks **only** status. A task is marked ✅ only with evidence. Approvals are never assumed.
> **Rule:** MVP first (Phases 0–4, 40 tasks). Deferred features (Later-L1…L9) start only after HC-8.

**Last updated:** 2026-11-14

Legend: ✅ done · ◐ partial · ⬜ not started · ⛔ blocked (needs approval/input)

## Summary

| Phase | Tasks | ✅ | ◐ | ⬜/⛔ | Progress |
|---|---|---|---|---|---|
| 0 — Foundation | 13 | 13 | 0 | 0 | 100% built and verified locally |
| 1 — Upload, STT, speakers, claims | 8 | 0 | 0 | 8 | 0% |
| 2 — Documents & contract verification | 7 | 0 | 0 | 7 | 0% |
| 3 — Finance, orchestration, reporting | 7 | 0 | 0 | 7 | 0% |
| 4 — Eval, security, release | 5 | 0 | 0 | 5 | 0% |
| **Total** | **40** | **13** | **0** | **27** | **~33% of tasks; ~20% of MVP effort** |

Effort note: Phase 0 is the plumbing. The core product (STT, claims, OCR, verification, finance) is Phases 1–3, so the task count overstates how much of the MVP is done.

## Human checkpoints

| ID | Status | Blocks |
|---|---|---|
| HC-1 | ✅ Approved 2026-11-13 | Phase 0 |
| HC-2 | ⛔ Pending | T1.4 onward (Phase 1 code) |
| HC-3 | ⬜ Pending (Phase 1 exit) | T3.1 |
| HC-4 | ⬜ Pending (Phase 2 exit) | T3.1 |
| HC-5 | ⬜ Pending (Phase 3 exit) | Phase 4; user-facing finance |
| HC-6 | ⬜ Pending (Phase 4) | User-facing regulatory findings only |
| HC-7 | ⬜ Pending | Real users; HC-8 |
| HC-8 | ⬜ Pending | Release |

Phase 1 start also needs the user's go-ahead (requested 2026-11-14; not yet given).

## Phase 0 — Foundation: exit report (2026-11-14)

| Task | Status | Evidence |
|---|---|---|
| T0.1 Repo, compose, settings, logging | ✅ | Q-16 resolved (MinIO built from source). `make up`: db, minio, api, worker healthy; minio-init created 3 private, SSE-S3 buckets. `/readyz` = `{"db":"ok","storage":"ok","listener":"ok"}`. Storage smoke test: put (SSE=AES256), presign, delete |
| T0.2 Migration 0001 | ✅ | upgrade→downgrade→upgrade test passes |
| T0.3 Error model + retry | ✅ | unit tests for all 4 error classes |
| T0.4 Auth + WS tickets | ✅ | integration tests pass against Postgres |
| T0.5 Sessions, consent, attestation, revocation, gate, audit | ✅ | integration tests pass |
| T0.6 Idempotency middleware | ✅ | replay / 409 / 400 / concurrency tests pass |
| T0.7 Job queue | ✅ | dedupe, lease reclaim, retry, cancellation tests pass |
| T0.8 Event bus + WS | ✅ | WS delivery < 200 ms test passes |
| T0.9 Audit service | ✅ | append-only trigger test passes |
| T0.10 Provider protocols + fakes | ✅ | interfaces + fakes; no real provider (by design) |
| T0.11 Frontend shell | ✅ skeleton | login, sessions, consent/attestation screens; 4 vitest tests; build OK. No browser E2E yet (planned with T1.7) |
| T0.12 CI | ✅ locally | `make ci` exit 0: ruff, mypy (39 files), **55/55 backend tests, 0 skipped**, frontend lint/typecheck/tests/build, dataset check. `REQUIRE_DB=1` makes a missing DB fail (verified: exit 4). ⚠ GitHub Actions run on branch `phase-0-closeout` not verified: the repo is private and `gh` is not installed |
| T0.13 Eval datasets + consent drafts | ✅ | 10 agreements AG01–AG10 (PDF + `terms.yaml`, all 6 traps, AG10 is a control); 101 terms checked on their stated pages; byte-identical regeneration. Consent/attestation texts en/hi are DRAFT (HC-7) |

**Carried forward (not Phase 0 blockers):**
- Print-and-scan copies and 3 phone photos of each agreement. These are manual; needed for E5 (Phase 2).
- Browser E2E tests (T1.7).
- Get the GitHub Actions run green on `phase-0-closeout`, then merge to `main`.

## Phase 1 — Upload, STT, speakers, claims (HC-2 at start, HC-3 at exit)

| Task | Status | Blocker |
|---|---|---|
| T1.1 Recording upload pipeline | ⬜ | Can start (needs only T0.x) |
| T1.2 ElevenLabs batch adapter + revisions | ⬜ | Needs ElevenLabs API key |
| T1.3 Speaker suggestion + confirmation | ⬜ | T1.2 |
| T1.4 Schema registry | ⛔ | HC-2 |
| T1.5 Normaliser | ⛔ | T1.4 |
| T1.6 Claim extraction | ⛔ | T1.2, T1.5, ADR-004 (LLM choice) |
| T1.7 Claims UI, transcript, snippets | ⬜ | T1.3, T1.6 |
| T1.8 Experiments E2, E3, E10 + LLM eval | ⬜ | T1.2, T1.6 |

## Phase 2 — Documents & contract verification (HC-4)

| Task | Status |
|---|---|
| T2.1 Document upload, sanitise, versioning | ⬜ (can start after Phase 0) |
| T2.2 Docling/OCR + confidence | ⬜ |
| T2.3 Clauses + doc_terms | ⬜ |
| T2.4 Chunks + hybrid retrieval | ⬜ |
| T2.5 Orchestrator v1 | ⬜ (needs T1.4 → HC-2) |
| T2.6 Contract engine | ⬜ |
| T2.7 Results dashboard v1 + evidence drawer | ⬜ |

## Phase 3 — Finance, orchestration, reporting (HC-5 at exit)

| Task | Status |
|---|---|
| T3.1 SourcedFactory + MVP formulas (synthetic only) | ⬜ (needs HC-3, HC-4) |
| T3.2 Merge v2 | ⬜ |
| T3.3 Results UI v2 + PDF report | ⬜ |
| T3.4 Ask-lite + routing | ⬜ |
| T3.5 Shadow rules + demo lenders | ⬜ |
| T3.6 Gated formulas (off for MVP) | ⬜ |
| T3.7 HC-6 rule review package | ⬜ |

## Phase 4 — Evaluation, security, release (HC-6, HC-7, HC-8)

| Task | Status |
|---|---|
| T4.1 Full eval + calibration | ⬜ |
| T4.2 Security controls + tests | ⬜ |
| T4.3 Retention, erase, audit completeness | ⬜ |
| T4.4 Accessibility, Hindi copy, UX test | ⬜ |
| T4.5 Staging deploy + load test | ⬜ |

## Open inputs needed from the user
- **HC-2** approval (claim schema, reason codes, CONFLICT evidence, wording; docs 04 and 08), which is the main blocker for Phase 1.
- ElevenLabs API key (T1.2).
- D9–D12: hosting region, team size and deadline, UI designs, malware scanning.
