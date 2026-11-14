# SaidSigned — MVP Task Status Tracker

> Companion to [MVP_IMPLEMENTATION_PLAN.md](MVP_IMPLEMENTATION_PLAN.md), which defines the tasks.
> This file tracks **only** status. A task is marked ✅ only with evidence. Approvals are never assumed.
> **Rule:** MVP first (Phases 0–4, 40 tasks). Deferred features (Later-L1…L9) start only after HC-8.

**Last updated:** 2026-11-14 (after the merge of `phase-0-closeout` into `main`, merge commit `0011ee7`)

Legend: ✅ done · ◐ partial · ⬜ not started · ⛔ blocked (needs approval/input)

## Summary

Task counts come from the plan's inventory (MVP_IMPLEMENTATION_PLAN.md §5: 40 tasks = 13 + 8 + 7 + 7 + 5).

| Phase | Tasks | ✅ | ◐ | ⬜ | ⛔ | Task completion |
|---|---|---|---|---|---|---|
| 0 — Foundation | 13 | 13 | 0 | 0 | 0 | 100% (13/13) |
| 1 — Upload, STT, speakers, claims | 8 | 0 | 0 | 5 | 3 | 0% (0/8) |
| 2 — Documents & contract verification | 7 | 0 | 0 | 7 | 0 | 0% (0/7) |
| 3 — Finance, orchestration, reporting | 7 | 0 | 0 | 7 | 0 | 0% (0/7) |
| 4 — Eval, security, release | 5 | 0 | 0 | 5 | 0 | 0% (0/5) |
| **Total** | **40** | **13** | **0** | **24** | **3** | **32.5% (13/40)** |

⛔ means blocked on an approval or input that the tracker names. ⬜ means not started; many ⬜ tasks still wait on earlier tasks (see the dependency graph in the plan §3).

Progress is reported as task completion only. Effort is not sized in any project document, so no effort percentage is given.

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

Checkpoint states are unchanged by implementation progress. HC-1 is the only recorded approval (plan header and §4). Phase 1 start also needs the user's go-ahead (requested 2026-11-14; not yet given).

## Phase 0 — Foundation: exit report (2026-11-14)

All 13 tasks meet their "Done when" criterion from the plan §5. Each row cites its own evidence. The evidence level for each check (local, GitHub, user-reported) is listed under **Verification evidence** below.

| Task | Status | Evidence | Limitations kept |
|---|---|---|---|
| T0.1 Repo, compose, settings, logging | ✅ | Q-16 resolved, option (b) (doc 14): MinIO/mc built from pinned upstream source (`infra/minio/Dockerfile`). `make up`: db, minio, api, worker healthy; minio-init created 3 private, SSE-S3 buckets. `/readyz` = `{"db":"ok","storage":"ok","listener":"ok"}`. Storage smoke test: put (SSE=AES256), presign, delete | Local stack only; no staging (T4.5) |
| T0.2 Migration 0001 | ✅ | upgrade→downgrade→upgrade integration test passes | — |
| T0.3 Error model + retry | ✅ | unit tests cover all 4 error classes | — |
| T0.4 Auth + WS tickets | ✅ | auth/session integration tests pass against Postgres | — |
| T0.5 Sessions, consent, attestation, revocation, gate, audit | ✅ | consent integration tests (gate, version/hash, revocation cancels jobs, audit rows) + attestation wording unit test pass | Consent gate is exercised through a test-only route until real gated endpoints arrive (T1.1) |
| T0.6 Idempotency middleware | ✅ | replay / 409 conflict / 400 missing / 409 in-progress / concurrency tests pass | — |
| T0.7 Job queue | ✅ | dedupe, lease reclaim, retry per class, cancellation-commits-nothing tests pass | — |
| T0.8 Event bus + WS | ✅ | WS delivery < 200 ms, resume with `since`, single-use ticket tests pass | — |
| T0.9 Audit service | ✅ | audit rows asserted; append-only trigger test passes | Audit completeness review is T4.3 |
| T0.10 Provider protocols + fakes | ✅ | `llm/base.py`, `modules/stt/base.py` + fakes | No real provider by design (T1.2; LLM choice is ADR-004) |
| T0.11 Frontend shell | ✅ (skeleton) | login/register, sessions, consent/attestation screens; events client; 4 vitest tests; production build OK. Login → session → consent was checked by an API-level smoke test through the Vite proxy (plan §5 status table, 2026-11-13) | **Not verified in a real browser**; the API-level smoke was not re-run on 2026-11-14. Browser E2E is planned with T1.7 |
| T0.12 CI (incl. feature-flag startup checks) | ✅ | Local: `make ci` exit 0 (details below). Startup checks: `check_features` in `core/config.py`, unit-tested. `REQUIRE_DB=1` turns a missing DB into a failure (verified locally: pytest exit 4). GitHub: user reported all runs green, including the latest on `phase-0-closeout` (see below) | GitHub result is user-reported, not agent-verified; the post-merge run on `main` (`0011ee7`) is unverified |
| T0.13 Eval datasets + consent drafts | ✅ | 10 fictional agreements AG01–AG10 (digital PDF + `terms.yaml`, all 6 doc 12 traps, AG10 is a control); checker validates all 101 terms on their stated pages and runs in `make ci` and the `eval-datasets` CI job; byte-identical regeneration. Consent/attestation texts en/hi drafted | Texts are **DRAFT** until HC-7. Scans/photos deferred (below) |

### Verification evidence

| Check | Where | Result | Source of evidence |
|---|---|---|---|
| `make ci` (ruff; mypy, 39 files; backend pytest; frontend lint/typecheck/vitest/build; agreement checker) | Local, Docker running | Exit 0. Backend **55 passed, 0 skipped** (21 unit + 34 integration). Frontend 4 tests passed. Checker: 101 terms, 0 problems | Agent run, 2026-11-14 |
| Docker compose stack | Local | db, minio, api, worker healthy; minio-init exited 0 | Agent run, 2026-11-14 |
| GitHub Actions on `phase-0-closeout` (jobs: backend with Postgres service and `REQUIRE_DB=1`, eval-datasets, frontend) | GitHub | All runs green, including the latest on `phase-0-closeout` (head `f276811`) | **Reported by the user, 2026-11-14.** Not independently verified by the agent (private repo; `gh` not installed). No run IDs are recorded in the repo |
| GitHub Actions on `main` after merge `0011ee7` | GitHub | Unknown | Not checked by anyone yet. The merge contains the same tree as `f276811` |

**Historical (superseded):** an earlier run on 2026-11-14, with Docker not running, gave 21 backend unit tests passed and 34 integration tests skipped ("Postgres not reachable"); MinIO was then blocked (Q-16) and `/readyz` reported `storage: error`. These are no longer current. The later run with Postgres up passed all 55, and `REQUIRE_DB=1` now prevents silent skips.

**Carried forward (not Phase 0 blockers):**
- Print-and-scan copies and 3 phone-photo variants of each agreement. These are manual; needed for E5 (Phase 2).
- Browser E2E tests (T1.7).
- Confirm the GitHub Actions run on `main` for merge commit `0011ee7`.
- Done (historical): `phase-0-closeout` merged into `main` as `0011ee7` on 2026-11-14 after the user reported green CI; the branch is kept, as the user asked.

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
