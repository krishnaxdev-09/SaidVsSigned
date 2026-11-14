# 14 — Architecture Decisions & Open Questions

> **Version 0.2.2 — 2026-11-13 (0.2.1: HC-1 scope listed; 0.2.2: HC-1 approval recorded).** Original: `_archive/v0.1-2026-11-13/14_…md`.
>
> **Status meanings:**
> - **Accepted:** decided by the user, with the date.
> - **Proposed:** awaiting the named checkpoint.
> - **Open:** needs information.
>
> No accepted decision from v0.1 was rewritten. Schedule changes are recorded in ADR-015.

## ADRs
| ADR | Title | Status | Notes / v0.2 change |
|---|---|---|---|
| 001 | Modular monolith + one worker | **Accepted (HC-1, 2026-11-13)** | Unchanged |
| 002 | Postgres as DB, vector store, job queue and event bus | **Accepted (HC-1, 2026-11-13)** | v0.2 adds `dedupe_key` and the idempotency table (doc 11 §2) |
| 003 | ElevenLabs Scribe behind `STTProvider` | Accepted (provider, from brief); fallback Open (HC-3) | MVP uses **batch** only; realtime deferred to Later-L1 |
| 004 | LLM provider | Open: provider-agnostic, accepted 2026-11-13 | Selection eval early in Phase 1 |
| 005 | Embedding model (reranker deferred) | Open (HC-4) | Dimension is a migration parameter; reranker → Later-L4 |
| 006 | Semantic CONFLICT needs NLI + LLM-judge agreement | Approved as the working design at HC-1 (2026-11-13); final confirmation at HC-2 | Unchanged |
| 007 | Status taxonomy CONFLICT/MATCH/UNCLEAR/NOT_VERIFIED | Accepted (brief; reaffirmed 2026-11-13) | v0.2 adds canonical reason codes, `FindingKind`, `REASON_STATUS` mapping (doc 08) |
| 008 | Audio capture modes: mic, Meet tab, upload; Meet API spike; no headless bots | Accepted 2026-11-13 | **Unchanged decision**; schedule moved by ADR-015 |
| 009 | Speaker attribution without realtime diarization | v0.1 Proposed → **Accepted 2026-11-13 for MVP**: CONFLICT requires a one-tap `user_confirmed` role | Live `inferred` roles remain Later-L1 |
| 010 | Deterministic finance; LLM only extracts | Accepted (brief) | v0.2: `SourcedFactory` enforcement; APR, prepayment and penal gated by HC-5 |
| 011 | Scope: loans MVP, insurance-ready | Accepted 2026-11-13 | Insurance → Later-L7 |
| 012 | Noise handling | Approved as the working design at HC-1 (2026-11-13); final confirmation at E9 evidence | MVP: loudness normalisation only; denoise only if E9 shows gains (Later-L8) |
| 013 | Docling + OCR backend | Open (HC-4) | Tesseract confidence pass if Docling has none |
| 014 | Rules as curated cited YAML with shadow mode | Approved as the working design at HC-1 (2026-11-13); final confirmation at HC-6 | v0.2: violation → `CONFLICT` + `finding_kind=regulatory`, visible only when enabled, reviewed and HC-6 approved; all MVP rules shadow |
| **015** | **Upload-first MVP; P0-1 Live Promise Verification deferred** | **Accepted 2026-11-13 (user instruction); confirmed at HC-1 2026-11-13** | See below |
| **016** | **MATCH requires the claim's primary comparison** | **Accepted 2026-11-13**; confirmed at HC-1 | Spoken-only consistency → sub-check note; inconsistency → `UNCLEAR(internal_inconsistency)` |
| **017** | **Document input = PDF + photo upload** | **Accepted 2026-11-13**; confirmed at HC-1 | In-app camera with edge guides deferred |
| **018** | **Canonical ownership of shared definitions** | **Accepted (HC-1, 2026-11-13)** | Statuses, reasons, evidence, thresholds → doc 08; tolerances → doc 06; retry and errors → doc 01; idempotency, events and DDL → doc 11; speaker model → doc 03 §5 |
| **019** | **`quote_confirmed_by_user` lifts the ASR gate** | Proposed (HC-3) | Fallback if batch confidences are absent or weak |

### ADR-015 detail
- **Context:** the user re-scoped the MVP to upload-first on 2026-11-13.
- **Conflict noted:** the original brief listed P0-1 as "Must Build", and the Problem Statement describes "real time" flagging and a live demo. This ADR records the **user's explicit override**. It does not hide it.
- **Consequence:**
  - The Problem Statement demo becomes "record on a phone → upload → photograph the printed form → results", instead of an on-screen live flag.
  - The batch pipeline is designed so that Later-L1 adds an ingestion front-end (realtime STT, incremental extraction) and reuses claims, engines, the orchestrator and the UI results.

### HC-1 approval scope and record
**HC-1: APPROVED** by the user (project owner) on 2026-11-13. HC-2…HC-8: **pending**.

HC-1 approved: Architecture and upload-first scope, specifically ADR-001, ADR-002, **ADR-015** (upload-first MVP, P0-1 live verification deferred — confirm), ADR-016, ADR-017, **ADR-018** (canonical definition owners) and the Proposed ADRs 006/012/014 as written. The accepted user decisions recorded in ADR-003, 007, 008, 009, 010 and 011 are presented for confirmation only. ADR-004, 005, 013 and 019 remain Open or Proposed for their own checkpoints.

## Open questions
| ID | Question | Status / owner |
|---|---|---|
| Q-1 | Do the PPT or UI designs exist? | Open — user |
| Q-2 | LLM provider (ADR-004) | Open — Phase 1 eval |
| Q-3 | Embedding model (ADR-005) | Open — HC-4 |
| Q-4 | Hosting region and data residency | Open — user/legal |
| Q-5 | Primary device (Android Chrome?) | Open — user |
| Q-6 | Agent notification method | **Deferred** (live modes, Later-L1); for uploads see Q-13 |
| Q-7 | Lender-side users | Deferred — Later-L9 |
| Q-8 | Second STT provider | Open — HC-3 |
| Q-9 | UI languages beyond en/hi | Open — user |
| Q-10 | Malware scanning (ClamAV) | Open — user/security |
| Q-11 | Retention period (30 days proposed) | Open — user + HC-7 |
| Q-12 | Team size and deadline | Open — user |
| **Q-13** | Policy when the uploader says the agent was **not** informed or is unsure | **Open — HC-7 legal**. MVP default: allow, warn, mark the report |
| **Q-14** | Allow a one-time retention extension? | Open — user |
| **Q-16** | **Phase 0 blocker (found 2026-11-13):** the pinned MinIO images (`minio/minio`, `minio/mc`) cannot be pulled from Docker Hub ("pull access denied") or Quay (401). The decision "S3-compatible storage, MinIO locally" is **unchanged**. Options: (a) you provide registry access or a mirror for MinIO; (b) build MinIO from source; (c) approve another S3-compatible local server. | **Resolved 2026-11-14 — option (b).** The user told the agent to resolve it ("do whatsoever"). MinIO and mc are built from the same pinned upstream release tags (`infra/minio/Dockerfile`, unmodified source, AGPL-3.0) and the compose stack is healthy, with SSE-S3 confirmed. The architecture decision is unchanged. |
| **Q-15** | Max speakers for diarization (2 default, up to 4?) | Open — HC-3 |

## Technical assumptions & experiments
| ID | Assumption | Experiment | MVP? | Blocks |
|---|---|---|---|---|
| A2 | Scribe batch handles Hinglish numbers ≥ 95% | E2 | ✅ | HC-3 |
| A3 | Batch returns usable word confidence | E3 | ✅ | ASR gate / ADR-019 |
| A5 | Docling + OCR reads phone photos ≥ 97% with obtainable confidences | E5 | ✅ | HC-4 |
| A6 | NLI + LLM-judge semantic CONFLICT precision ≥ 0.9; hybrid R@5 ≥ 0.9 | E6 | ✅ | HC-4 |
| A7 | APR method matches the RBI KFS illustration | E7 (shadow) | ✅ (gated) | Later-L6 |
| A9 | Denoising doesn't help or hurt uploads | E9 | Optional | ADR-012 |
| A10' | Diarization + auto-role ≥ 90% on two-speaker mono | **E10 (new)** | ✅ | HC-3 |
| A11 | GST on processing fees = 18% | Doc check | ✅ | HC-5 |
| A13 | Penal-charges circular content | Doc check | Shadow | HC-6 |
| A14 | Docling bbox origin and units | Unit test on fixtures | ✅ | Phase 2 |
| A15 | Provider retention/no-training settings available | Doc check | ✅ | HC-7 |
| A1 | Realtime audio enum/latency | E1 | Later-L1 | — |
| A4 | Chrome tab-audio stability | E4 | Later-L2 | — |
| A8 | Meet Media API enrollment | E8 | Later-L3 | — |
| A12 | RBI lists parseable | Spike | Later-L5 | — |
