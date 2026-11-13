# Cross-Document Consistency Report — v0.2.2 (2026-11-13)

> **v0.2.2.** Sections A–C are the historical v0.2 findings, kept unchanged. Section D is the v0.2.1 re-check (plus D.1, HC-6 timing). Section E is the v0.2.2 pre-implementation audit. HC-1 was approved by the user on 2026-11-13; HC-2…HC-8 are pending.

**Method:**
1. Manual review of all 14 docs and the master report.
2. A scripted check (Python regex over all v0.2 docs) for:
   - reason codes used as `STATUS(code)` against doc 08's canonical table
   - status variants outside the four statuses
   - threshold names
   - `speaker_source` literal sets in docs 04, 08 and 11
   - removed v0.1 field names
   - stale phase labels

**Result of the final scripted pass:**
- 0 non-canonical reason codes.
- 0 status variants, apart from historical notes explaining the removal of `CONFLICT_REGULATORY_NOTE`.
- Threshold names limited to the canonical set (`T_asr_num`, `T_asr_avg`, `T_ocr`, `T_ocr_num`, `T_ret`, `T_nli_*`).
- `speaker_source` sets are identical in docs 04, 08 and 11.
- Remaining mentions of `quote_span`, `asr_confidence` and "±10%" are deliberate change notes or the rounding band.
- 0 stale "P<n>" phase labels.

## A. Issues requested by the user
| # | Issue | Resolution | Where |
|---|---|---|---|
| 1 | `calc_not_converged` missing from ReasonCode | Added to the canonical table as NOT_VERIFIED; five other codes in use but undefined were also added | 08 §3.1; 06 §4 |
| 2 | `CONFLICT_REGULATORY_NOTE` not a status | → `CONFLICT` + `finding_kind=regulatory`. Visible only if rule enabled + `reviewed_by` + HC-6 approval; otherwise `NOT_VERIFIED(rule_not_enabled)` stored in `shadow_verdicts` and excluded from merge and UI | 08 §3.2, §7; 07 §3.3; 11 (`shadow_verdicts`, `rules.hc6_approved_at`) |
| 3 | Speaker and numeric confidence gates misaligned | Canonical speaker model in 03 §5 (`speaker_source` + `role_assignment`). One gate order and one threshold table in 08 §5. Claim carries `asr_min_numeric_conf`. Gates apply to every spoken calculation input via `Sourced.gate_ok` | 03 §5; 04 §4; 06 §3; 08 §5 |
| 4 | CONFLICT evidence | Allowed combinations C-DOC and C-CALC-DOC (C-RULE shadow only). Spoken evidence must be agent + `user_confirmed` + gates passed. DB integrity validator. Quote anchors pinned to a revision | 08 §4; 04 §4 |
| 5 | Finance only from validated `Sourced` | `SourcedFactory` with a private token; `UnvalidatedInputError`; inputs only from normalised DB rows | 06 §3 |
| 6 | Numeric questions → finance | Ask router; finance route uses document terms via the factory; validator rejects numbers not present in quotes | 09 §5 |
| 7 | Idempotency and duplicates | HTTP keys, content-hash dedupe, job `dedupe_key`s, idempotent per-version handlers, events after commit | 11 §2; 02 §5; 03 §6; 04 §8; 05 §6 |
| 8 | Error/retry consistency | One retry policy by error class; one API error model; engine failure → `NOT_VERIFIED(engine_error)`; schema failure → 1 corrective retry | 01 §6–7; referenced by 02–09 |
| 9 | Consent, recording authority, retention, deletion, ownership, audit | Attestation; revocation cancels jobs; retention purge; erase with tombstone; ownership filter; audit action list; open legal items listed for HC-7 | 01 §5; 12 §B; 11 §3 |
| 10 | Reason codes, schemas, evidence, transitions | Single registry in 08. DDL in 11 mirrors 04/08 (parity test). `StatusChange` triggers and versioning | 08 §3, §9; 11 §3, §7 |

## B. Additional inconsistencies found and resolved
| ID | Issue (v0.1) | Resolution |
|---|---|---|
| X1 | Approx tolerance ±10% (doc 05) vs ±5% (doc 06) | Single table in 06 §5 |
| X2 | Diarized uploads with LLM-assigned roles could yield CONFLICT (only `inferred` was gated) | `role_assignment` + one-tap confirmation (user decision) |
| X3 | `quote_span` offsets into a transient LLM window could not be audited against the DB | `quote_anchors` per segment and revision |
| X4 | Finance MATCH possible without any document | Primary-comparison rule (user decision); `internal_inconsistency` |
| X5 | RAG citation validator would reject correct finance numbers and allowed LLM arithmetic paths | Routing + strict validator |
| X6 | "P0" meant both a phase and the innovations | Phase 0–4 / Later-Lx naming |
| X7 | Doc 10 events (`transcript.partial`, …) not matching an upload flow; doc 11 lacked `recording.status`, `transcript.ready`, `report.status` | Canonical list in 11 §5; 10 §6 aligned |
| X8 | Erasure test "no rows remain" contradicted keeping consent records | Tombstone defined in 11 §3 and 12 §B.5 |
| X9 | `chunks.embedding vector(1024)` hard-coded while ADR-005 is open | Migration parameter |
| X10 | Transcript `revision` (doc 03) vs `is_current` (doc 11); claims not pinned | `transcript_revisions` table; claims pin a revision; speaker confirmation is not a revision |
| X11 | `calc_not_converged` was UNCLEAR | NOT_VERIFIED (missing evidence) |
| X12 | APR note exposed before validation | Hidden until HC-5 |
| X13 | Master report threshold name `T_asr` and v0.1 phase labels | Updated |
| X14 | Doc 05 "page unreadable → NOT_VERIFIED" had no code; doc 04 "unparsed → UNCLEAR" had no code; doc 07 "not found" had no code | `page_unreadable`, `value_unparsed`, `entity_not_found` |
| X15 | Engine timeouts: doc 08 v0.1 gave contract 15 s, but Docling-backed retrieval can exceed that | Contract 30 s; ingestion is a separate job, so verification only reads indexed data |
| X16 | Doc 01 consent gate assumed live-mode agent consent | Upload attestation; agent consent deferred with live |

## C. Known residuals (not inconsistencies, but to watch)
- Master report §7–§18 still describe the full architecture, including live components, as background. The v0.2 banner states the docs win. A full master rewrite is optional at HC-1.
- Rule YAML `quote` fields are intentionally `null` until HC-6.
- Thresholds and tolerances are **initial proposals**, not calibrated (Phase 4).
- ElevenLabs batch parameter names must be re-verified against the current API at implementation time (doc 03 §3).

---

## D. v0.2.1 re-check (2026-11-13)
| Check | Result |
|---|---|
| HC-1 … HC-8 each explicitly present in the plan, doc 13 and the master report | Verified by script (below) |
| HC-6 in the plan's dependency graph, checkpoint sequence and roadmap | Verified |
| Plan task count stated = task rows | Verified (40 = 13+8+7+7+5) |
| ADR-015 and ADR-018 in the HC-1 scope (plan, doc 13, doc 14, master); ADR-018 defined in doc 14 | Verified |
| Attestation never described as agent consent | Verified by search; every attestation mention is framed as a customer statement |
| `calculation_not_enabled` meaning consistent across docs 05, 06, 08, 09 | Verified |
| Reason codes / status variants / thresholds (v0.2 checks re-run) | 0 non-canonical uses |
| Archive v0.1 unchanged | Checksum identical to the original |

**New residuals:** consent and attestation texts exist only as drafts to be written in T0.13. Their use with real users requires HC-7.

### D.1 HC-6 timing clarification (v0.2.1 final)
All of these files now state the same HC-6 timing: plan §4/§5, doc 13 Phase 3 and §4, doc 07 §2, and the master report §20.
- T3.7 is completed in Phase 3 and produces the review package.
- The HC-6 approval checkpoint is in Phase 4, after the package is ready.
- HC-6 does not block HC-8.
- Rules not approved by HC-6 stay in shadow mode.

The script check found no statement placing HC-6 approval in Phase 3.

## E. v0.2.2 pre-implementation audit (2026-11-13)
**Method:**
- Full reads of docs 01 and 11, the Phase 0 contracts.
- Targeted source searches across all docs for the 10 contract areas the user listed.
- The scripted checks from sections A–D re-run.

**Findings A1–A18** are listed with fixes in `CHANGELOG.md` (v0.2.2). Summary by area:

| Area | Result |
|---|---|
| 1 Canonical ownership | Unchanged (ADR-018). New Phase 0 models placed in doc 11 §4a; error codes extended in doc 01 §7 |
| 2 API / idempotency | A2–A5, A15 fixed |
| 3 DB / deletion / ownership | A1, A4, A12, A13 fixed |
| 4 Consent / revocation / cancellation | A6, A9–A11, A15 fixed; behaviour unchanged from v0.2.1 |
| 5 Audio / transcripts / speakers / claims | No contradictions found (Phase 1 contracts). The A7 job fix also covers `claims.extract` and `stt.batch` |
| 6 Documents / OCR / evidence / replacement | No contradictions found |
| 7 Finance gate | No contradictions found (doc 06 §4a consistent with 05/08/09) |
| 8 Verification semantics | No contradictions found. **A7 removes a real risk of lost `verify` triggers** |
| 9 HC dependencies | HC-1 approval recorded; HC-2…HC-8 pending; HC-6 timing as in D.1 |
| 10 MVP vs deferred | A14 corrected; no scope expansion |

No finding required a user decision. All were contract completions within accepted decisions.
