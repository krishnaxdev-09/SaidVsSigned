# SaidSigned — MVP Task Status Tracker

> Companion to [MVP_IMPLEMENTATION_PLAN.md](MVP_IMPLEMENTATION_PLAN.md), which defines the tasks.
> This file tracks **only** status. A task is marked ✅ only with evidence. Approvals are never assumed.
> **Rule:** MVP first (Phases 0–4, 40 tasks). Deferred features (Later-L1…L9) start only after HC-8.

**Last updated:** 2026-11-14

Legend: ✅ done · ◐ partial · ⬜ not started · ⛔ blocked (needs approval/input)

## Summary

| Phase | Tasks | ✅ | ◐ | ⬜/⛔ | Progress |
|---|---|---|---|---|---|
| 0 — Foundation | 13 | 12 | 1 | 0 | ~95% (not marked complete) |
| 1 — Upload, STT, speakers, claims | 8 | 0 | 0 | 8 | 0% |
| 2 — Documents & contract verification | 7 | 0 | 0 | 7 | 0% |
| 3 — Finance, orchestration, reporting | 7 | 0 | 0 | 7 | 0% |
| 4 — Eval, security, release | 5 | 0 | 0 | 5 | 0% |
| **Total** | **40** | **12** | **1** | **27** | **~31% of tasks; ~15–20% of MVP effort** |

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

## Phase 0 — Foundation

| Task | Status | Evidence / gap |
|---|---|---|
| T0.1 Repo, compose, settings, logging | ✅ built | ⚠ compose `up` blocked by Q-16 (MinIO images); `/readyz` reports `storage: error` |
| T0.2 Migration 0001 | ✅ | `0001_foundation`; upgrade→downgrade→upgrade test |
| T0.3 Error model + retry | ✅ | `core/errors.py`, `core/retry.py`; unit tests pass |
| T0.4 Auth + WS tickets | ✅ | integration tests (need Postgres) |
| T0.5 Sessions, consent, attestation, revocation, gate, audit | ✅ | integration tests (need Postgres) |
| T0.6 Idempotency middleware | ✅ | integration tests (need Postgres) |
| T0.7 Job queue | ✅ | integration tests (need Postgres) |
| T0.8 Event bus + WS | ✅ | integration tests (need Postgres) |
| T0.9 Audit service | ✅ | append-only trigger test |
| T0.10 Provider protocols + fakes | ✅ | interfaces + fakes only; no real provider |
| T0.11 Frontend shell | ✅ skeleton | login, sessions, consent screens; no browser E2E yet |
| T0.12 CI | ✅ locally | `make ci` green; workflow not yet run on GitHub |
| T0.13 Eval datasets + consent drafts | ◐ | DRAFT texts en/hi done; **synthetic eval datasets not started** |

**Verification on 2026-11-14:** `make ci` passes (ruff, mypy, frontend lint/typecheck/tests/build). Backend: 21 unit tests passed; **34 integration tests skipped** because Docker/Postgres was not running. Re-run with `make db && make ci` to confirm them.

**To close Phase 0:**
1. Resolve Q-16 (MinIO image) so `make up` is healthy.
2. Run the integration tests against Postgres (all 34 must pass).
3. Push and get the GitHub Actions run green.
4. Draft the 10 synthetic agreements (T0.13).

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
- Q-16: the MinIO image choice.
- D9–D12: hosting region, team size and deadline, UI designs, malware scanning.
