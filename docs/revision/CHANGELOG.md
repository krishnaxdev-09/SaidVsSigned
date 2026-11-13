# SaidSigned Documentation Change Log — v0.1 → v0.2 (2026-11-13)

- **Trigger:** the user instructed an upload-first MVP scope and asked for cross-document issues to be resolved.
- **Originals:** preserved unchanged in `docs/_archive/v0.1-2026-11-13/` (15 files; checksums verified identical at copy time).
- **User decisions applied** (2026-11-13):
  1. CONFLICT requires a one-tap user-confirmed agent role.
  2. MATCH requires the claim's primary comparison. Spoken-only consistency becomes NOT_VERIFIED + note.
  3. Document input is PDF + photo upload.
  4. Proceed with the revision, including the master report.

**Nothing has been implemented.** No approvals, credentials, test results or legal reviews are recorded as obtained.

## Global changes (all docs)
| Change | Why |
|---|---|
| Version header + scope tags `[MVP]` / `[MVP-limited]` / `[Deferred → Later-Lx]` | Distinguish MVP from future scope |
| Phases renamed **Phase 0–4** and **Later-L1…L9**; "P0-x" now refers only to innovations | v0.1 used "P0" for both a phase and the innovations |
| Canonical ownership of shared definitions (ADR-018) | Removes duplicated, drifting definitions |
| Deferred designs kept as "Deferred designs (preserved)" sections, with full text in the archive | User asked not to delete future architecture |

## Per-document changes
| Doc | What changed | Why |
|---|---|---|
| **01** | Upload-only session mode (`mode_not_available` for live). **Recording attestation** (`i_was_party`, `agent_informed`) in the consent model. Consent revocation cancels jobs. **Canonical retry policy** (transient/schema/input/internal). **Canonical API error codes**. `features.yaml`. `idempotency_keys` and `deletion_receipts` in migration 0001. Audio WS ticket deferred | Issues 8, 9; upload-first scope |
| **02** | Rewritten around upload: magic-byte validation, streaming sha256, content dedupe, deterministic object keys, replace-recording flow, snippet endpoint, `no_speech_detected`. Live mic, Meet tab, Meet API and headless/cellular moved to preserved "Deferred designs" D1–D4 | Scope; issue 7 |
| **03** | Batch-only MVP. **Canonical speaker model**: `speaker_source` + `role_assignment` + one-tap confirmation; stereo L/R mapping. Transcript revisions defined (speaker confirmation is not a revision). Hinglish policy tied to E2. New experiment E10. Realtime adapter preserved as deferred | Issue 3; decision 1 |
| **04** | `quote_span` (window-relative) → **`quote_anchors`** (segment + revision + char offsets). Added `asr_min_numeric_conf`, `role_assignment`, `transcript_revision`, `normaliser_version`, `state`, `LENDER_IDENTITY`. `asr_confidence` renamed `asr_avg_conf`. LLM-provided `value` explicitly discarded. Revision/supersede/idempotent re-run rules. Confirm-quote endpoint (proposed) | Issues 3, 4, 5, 7, 10 |
| **05** | Photos via file upload (no custom camera). Document versioning + page replace. Content-hash dedupe. Atomic per-version writes. `page_unreadable` reason. Approximate tolerance now references doc 06 (v0.1's ±10% removed). GST and assumption-dependent handling. Evidence built only from DB rows. bbox normalisation stated as an assumption | Issues 4, 7, 10; decision 3; extra issue X1 |
| **06** | **`SourcedFactory`** as the only way to create inputs, with `UnvalidatedInputError`. MVP formula set. APR, flat-to-effective, prepayment and penal **gated off** until HC-5 (shadow traces only). **Canonical tolerance table** with rounding bands. `calc_not_converged` now NOT_VERIFIED. Spoken-only checks are `calculation_internal` sub-checks | Issues 1, 5; §5 of instruction; decision 2 |
| **07** | `CONFLICT_REGULATORY_NOTE` removed → `CONFLICT` + `finding_kind=regulatory`, visible only when enabled + reviewed + HC-6. All MVP rules shadow (`shadow_verdicts`). Public data limited to ≤ 5 curated demo lenders, never CONFLICT. Rule quotes left null pending HC-6 | Issue 2; scope |
| **08** | Became the **schema registry**: ReasonCode table (25 codes, each mapped to one status, with question templates; new: `calc_not_converged`, `calculation_not_enabled`, `page_unreadable`, `rule_not_enabled`, `entity_not_found`, `value_unparsed`, `internal_inconsistency`). `FindingKind`. Evidence models with quote anchors. **Allowed CONFLICT evidence combinations**. DB integrity validator. Canonical thresholds + gate order. Primary-comparison table. Merge algorithm (an engine error can never yield MATCH). `StatusChange`/versioning. Wording lint incl. Hindi | Issues 1–4, 6 (behaviour), 10; decisions 1–2 |
| **09** | Retrieval MVP without reranker. **Ask routing**: quote / finance / out_of_scope. Numeric answers only via doc 06. Validator rejects numbers not in quotes. Embedding dim parameterised. Version-aware retrieval | Issue 6; extra issue X5 |
| **10** | Rebuilt for the upload-first flow (consent/attestation → upload → speaker confirm → claims → documents → results → report). Live UI preserved as deferred. Event list aligned with doc 11 | Scope; extra issue X7 |
| **11** | **Canonical idempotency section** (HTTP keys, content dedupe, job dedupe keys per kind, idempotent handlers, events after commit). DDL rewritten to mirror the doc 04/08 models: `transcript_revisions`, `speaker_assignments` (role_assignment), claim quote anchors, document versions, `shadow_verdicts`, `reports`, `jobs.dedupe_key`/`error_class`, `deletion_receipts`. Erasure tombstone. Canonical event list (live events deferred) | Issues 7, 9, 10 |
| **12** | Datasets and metrics for upload-first (+ E10, routing, clarification coverage, batch latency). Retention, audit action list, deletion + tombstone, ownership. Legal items listed for HC-7 (incl. Q-13). Single-VM deployment | Issue 9 |
| **13** | Roadmap reorganised into Phase 0–4 with dependencies, deliverables, tests, acceptance, gates and exit conditions. Later-L1…L9 table. P0 tracker states P0-1 deferred. HC-1…HC-8 preserved and re-scoped; HC-6 only needed to make rules visible | §7 of instruction |
| **14** | ADR table with statuses. New ADR-015 (upload-first, P0-1 deferred — conflict with brief and Problem Statement recorded), ADR-016 (MATCH primary comparison), ADR-017 (PDF + photos), ADR-018 (canonical owners), ADR-019 (confirm-quote, proposed). ADR-009 accepted. New Q-13…Q-15. Experiments split MVP vs Later | Decision traceability |
| **Master** | v0.2 banner + §0 MVP scope table. C7/C8 conflicts added. P0-1 marked deferred. §19/§20 replaced with the v0.2 roadmap and checkpoints. Engine/experiment phase labels and threshold names updated. Other sections retained as background; banner states the docs win on any difference | Keep the master consistent |

## Notable semantic changes (review carefully)
1. `calc_not_converged`: UNCLEAR (v0.1 doc 06) → **NOT_VERIFIED** (missing evidence, not ambiguity).
2. Approximate-claim tolerance: doc 05 ±10% vs doc 06 ±5% → a **single table**: MATCH ±5%, rounding band ±10%.
3. An inferred or auto speaker role no longer allows CONFLICT. v0.1 only gated `inferred`; diarized auto roles slipped through.
4. Finance internal inconsistency without a document: possible CONFLICT in v0.1 → **UNCLEAR(internal_inconsistency)**.
5. APR "informational note": visible in v0.1 → **hidden until HC-5**.
6. Erasure: "no rows remain" → **tombstone retained** (consent receipt + audit + deletion receipt).

---

# v0.2 → v0.2.1 (2026-11-13) — corrections from your review of the implementation plan
Archived v0.1 files were **not** modified. No checkpoint was approved.

| # | Your request | Change | Files |
|---|---|---|---|
| 1 | Restore HC-6 | HC-6 is now an explicit checkpoint in the graph, the checkpoint sequence (order 6) and the roadmap. It governs approval of curated regulatory rules **and** any user-facing regulatory finding. Rules stay shadow until approved per rule (`reviewed_by`, `hc6_approved_at`). Scheduled in Phase 4 on the new T3.7 package. Does not block HC-8 | plan §3–5, §8; doc 13 (Phase 3/4, graph, HC table); doc 07 §2; master §19/§20 |
| 2 | Task count | v0.2 plan had **39** tasks. The "40" was my error in the chat summary; no doc stated it. Added the genuinely missing **T3.7 HC-6 rule review package** (rule YAML `quote` fields were null and no task produced the HC-6 inputs). Count is now **40** (13/8/7/7/5), stated in plan §5 | plan |
| 3 | ADR-015 in HC-1; verify ADR-018 | HC-1 scope now lists ADR-001, 002, **015**, 016, 017, **018**, plus Proposed 006/012/014; accepted decisions presented for confirmation. ADR-018 verified present in doc 14 (ADR table row 018) | plan §4/§8; doc 13 HC table; doc 14 new "HC-1 approval scope"; master §20 |
| 4 | Consent and attestation | Doc 01 §5 rewritten: versioned `consent_texts` with hashes, consent record fields and timestamps, attestation as a **customer self-declaration, not proof of agent consent**, revocation in one transaction with job cancellation (`cancelled` status, `cancel_requested_at`, step-boundary checks, enqueue refusal), audit events. Schema added in doc 11; wording in docs 10/12; report text "The customer states … (not independently verified)" | docs 01, 10, 11, 12; plan §6 |
| 5 | HC-5 finance gate | New doc 06 §4a: before HC-5, all formulas may be implemented and tested on synthetic data; every user-facing finance output is disabled (`calculation_not_enabled`). After HC-5, only the MVP set becomes visible. APR, flat-to-effective, prepayment and penal stay disabled for the whole MVP. Enforced by `features.yaml` flags and a startup check. Aligned docs 05 (formula-dependent conversions), 08 (reason-code meaning, §6 note), 09 (finance route) | docs 05, 06, 08, 09, 13; plan §7 |

Doc headers bumped to **v0.2.1** where changed: 01, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, master.

### v0.2.1 final touch (2026-11-13)
- **HC-6 timing:** a single explicit statement now appears in the plan §4 and the T3.7 row, doc 13 (Phase 3 deliverable and §4), and doc 07 §2.
- **Consistency report:** retitled to v0.2.1. The historical v0.2 sections and the re-check section are kept, and D.1 is added.
- No checkpoint approved, no code written, archive untouched.

---

# v0.2.1 → v0.2.2 (2026-11-13) — pre-implementation audit and HC-1 record
**HC-1 approved by the user on 2026-11-13.** HC-2…HC-8 remain pending. The archive is untouched, no accepted decision was changed, and MVP scope was not expanded.

| ID | Finding (verified in source) | Fix | Files |
|---|---|---|---|
| A1 | Erasure keeps the `sessions` row as a tombstone, so `ON DELETE CASCADE` never fires; `events`/`jobs` have no FK. "Cascade" erase would leave data | Explicit per-table delete list; content columns of `sessions` cleared | 11 §3 |
| A2 | Idempotency in-flight detection, hash scope, missing header and error storage undefined | Hash = method+path+body; `idempotency_key_required`, `idempotency_in_progress`; 60 s abandonment; only 2xx stored | 11 §2, 01 §7 |
| A3 | No 401 code; no codes for invalid state, in-flight key, missing key, duplicate email | Added `unauthorized`, `invalid_state`, `idempotency_in_progress`, `idempotency_key_required`, `email_taken` | 01 §7 |
| A4 | Ownership failure response unspecified | 404 `not_found` (no existence leak) | 01 §7 |
| A5 | Phase 0 request/response models missing; `product_type` enum dropped in v0.2 | New doc 11 §4a; enum restored from v0.1; DB CHECKs | 11 §3–4a, 01 §4 |
| A6 | Transient `created` state with no trigger | New sessions persist as `consent_pending`; Phase 0 transitions defined | 01 §4, 11 §3 |
| A7 | **Job dedupe over queued+running drops triggers that arrive during a run** (lost re-verification) | Coalescing: unique over `queued` only + claim skips keys with a running job | 11 §2–3, 01 §4 |
| A8 | No lease/visibility timeout: crashed workers leave jobs `running` forever | `lease_expires_at`, heartbeat, reclaim as a transient attempt | 11 §2–3, 01 §4 |
| A9 | Events-WS consent requirement ambiguous (close code 4403) | Events WS needs auth + ownership only; 4403 is audio WS only (Later-L1) | 01 §7, 11 §5 |
| A10 | "Test event" in Phase 0 acceptance undefined | `session.status{ready}` on consent commit | 01 §11, 11 §5 |
| A11 | Phase 0 acceptance required upload endpoints that only exist in Phase 1 | Consent-gate dependency verified on a test-only route | 01 §11 |
| A12 | Refresh-token rotation described but no reuse handling or schema | `family_id`, `replaced_by`; reuse revokes the family | 11 §3–4a |
| A13 | `REVOKE` doesn't make `audit_log` append-only for the owning role | Trigger blocks UPDATE/DELETE | 11 §3, 12 §B.4 |
| A14 | `DELETE /sessions/{id}` listed as Phase "0/4", but erase is task T4.3 | Phase 4 | 11 §4 |
| A15 | Whether consent and attestation are one request or two was ambiguous | One `POST /consents` carries both | 11 §4a |
| A16 | Audit list missing register/logout/session.create/refresh reuse | Added | 12 §B.4 |
| A17 | Tooling: pnpm unavailable; npm present | npm (pnpm optional) | 01 §2 |
| A18 | "CI green" needs a remote; none exists | Defined as `make ci` locally until a remote exists, reported honestly | 01 §11 |

**HC-1 record** added to: docs 01 (§2, §12), 13 (header, HC table), 14 (ADR statuses + HC-1 record), plan (header, §4, D1, T0.1) and master (status, §20).
- ADR-001/002/018 → Accepted at HC-1.
- ADR-015/016/017 confirmed at HC-1.
- ADR-006/012/014 approved as the working design, with final confirmation at HC-2 / E9 / HC-6.

### v0.2.2 implementation sync (2026-11-13, Phase 0)
- Doc 11 DDL: added the `consents_one_active_customer` partial unique index (enforces "one active customer consent", which the 409 rule in §4a assumes) and the TRUNCATE trigger on `audit_log`.
- Doc 14: new **Q-16**, a Phase 0 blocker: MinIO images can't be pulled. The decision is unchanged; your input is needed.
- Plan: added a Phase 0 per-task implementation status. Phase 0 is **not** marked complete.
