# 12 — Testing, Security, Privacy & Deployment

> **Version 0.2.1 — 2026-11-13 (upload-first MVP revision; consent wording and audit actions in 0.2.1).** Original: `_archive/v0.1-2026-11-13/12_…md`.
> **Scope:** [MVP] the test strategy and eval harness for the upload pipeline; security; consent; retention, deletion, ownership and audit for uploads; single-VM deployment. [Deferred] live-audio tests (E1, E4) and multi-instance WS (Later-L1).
>
> **Canonical here:** eval targets, the security control list, and the retention, deletion and audit requirements.

## Part A — Testing & evaluation

### A.1 Datasets (built in Phases 0–1, in `eval/`)
1. **10 synthetic loan agreements / KFS documents**, each as a digital PDF, a print-and-scan, and phone photos in 3 lighting conditions, with ground-truth `terms.yaml`. They contain traps:
   - "2% + GST"
   - fees in footnotes
   - an insurance premium deducted from the disbursement
   - flat rate in small print
   - prepayment charges on a floating-rate loan
   - a penal-interest clause
2. **30 scripted recorded conversations**:
   - 15 English and 15 Hinglish
   - 20 mono and 10 stereo-split
   - include imprecision traps
   - labelled claims and expected statuses against paired documents
3. **200 claim–clause pairs**, balanced across statuses.
4. **Q&A set:** 150 retrieval questions, 60 answer questions (15 unanswerable), 50 routing questions.
5. **Perturbation sets:** noise at 10 and 5 dB SNR, OCR digit swaps, injected low ASR confidence.

No real customer data is used without a separate consent and anonymisation.

### A.2 Test layers
| Layer | Tooling | MVP scope |
|---|---|---|
| Unit | pytest + hypothesis; vitest | Normaliser, formulas, `SourcedFactory`, gates/merge/validators, retry classifier, reducers, wording lint |
| Integration | testcontainers (pgvector, MinIO) | Jobs + dedupe, event bus, ingestion on fixtures, erase |
| API | httpx + schemathesis | Auth, ownership, error codes, idempotency |
| Events WS | websockets client | Ticket, resume, ordering |
| Provider contract | Recorded cassettes (ElevenLabs batch, LLM) | No network in CI |
| E2E | Playwright with fake providers | Full upload-first flow (doc 10 §9) |
| Eval harness | `eval/run.py` with real providers (manual/nightly) | Metrics in A.3 → versioned reports |

### A.3 Metrics (reported separately, each with a dated report)
| Component | Metric | MVP target [Proposed] | Experiment |
|---|---|---|---|
| Speech recognition | WER (en), CER and **numeric-token accuracy** (Hinglish) | WER ≤ 12% (clean en); numeric ≥ 95% | E2 |
| Speaker attribution | Auto-role accuracy; confirmation completion rate | Report; ≥ 90% auto | E10 |
| Claim extraction | P/R per type; false-promise rate; value exact-match | P ≥ 0.85, R ≥ 0.80, FP ≤ 5% | — |
| OCR | Key numeric field accuracy per rendering | Digital 100%; good-light photos ≥ 97% | E5 |
| Retrieval | Recall@5, MRR | R@5 ≥ 0.90 | E6 |
| Contradiction detection | Per-status confusion matrix; **CONFLICT precision** | ≥ 0.90 | E6 + Phase 4 |
| Finance | Golden exact match | 100% | — |
| Uncertainty | Injected low-confidence → CONFLICT | 0% | Phase 4 |
| Clarification coverage | Non-MATCH results with a question | ≥ 95% | — |
| Ask (limited) | Numeric questions → finance route; invalid-citation answers | ≥ 95%; 0 | — |
| Pipeline latency (batch) | Upload of 10 min of audio → results available | Measure; target ≤ 5 min p50 | — |

**Rule:** no accuracy figure appears in docs, the UI, the README or demos without a report in `eval/reports/` (dataset version, config/thresholds hash, date). **No test results exist yet.**

## Part B — Security, consent & privacy

### B.1 Consent & recording authority (uploads) [MVP]
Canonical in doc 01 §5:
- versioned consent and attestation texts (hash stored)
- the customer consent record with timestamps
- the customer attestation (`i_was_party` required, `agent_informed` as declared)
- revocation with processing cancellation
- audit entries

**The attestation is the customer's self-declaration. It is not independent proof that the agent consented or was informed.** Reports show it as "The customer states … (not independently verified)". **Legal position is not assumed** — see B.6.

### B.2 Technical controls
| Area | Control |
|---|---|
| Transport | TLS 1.2+, HSTS, WSS |
| AuthN | argon2id; 15-min access JWT; rotating refresh cookie (httpOnly, Secure, SameSite=Strict); single-use WS tickets |
| AuthZ / ownership | Single owner per session. Every repository query filters by `owner_id`. Presigned URLs are issued only after the ownership check. Optional RLS. Tested with cross-user attempts |
| Uploads | Streaming size limits; magic-byte sniffing; ffprobe/Docling in the worker with CPU/time limits; PDF sanitising (pikepdf); image re-encode + EXIF strip; decompression-bomb guards; ClamAV optional [Q-10] |
| Storage | Private buckets, SSE, presigned GET ≤ 5 min, deterministic keys per session |
| LLM / STT data minimisation | Redact PAN, Aadhaar (Verhoeff-checked), account, phone and email numbers before LLM calls. Send only the needed windows or clauses. Use providers' no-training / zero-retention settings where offered **[A]**. Audio must go to ElevenLabs: disclosed in the consent text |
| Secrets | Env / secret manager only; never logged |
| Logging | Structured; ids only; no transcript or document text; PII scrubber |
| Rate limits | Uploads 20/h, QA 60/h, sessions 20/day per user |
| Frontend | Strict CSP; no third-party scripts; SW excludes API and media |
| Supply chain | pip-audit, npm audit, Dependabot |

### B.3 Retention [MVP; period pending Q-11]
- `sessions.retention_until = created_at + RETENTION_DAYS` (30 days proposed).
- A nightly `retention.purge` job runs `session.erase` for expired sessions.
- The user can extend retention once (+30 days) [Proposed] or delete now.

### B.4 Audit (append-only `audit_log`)
**Actions:**
- `auth.register`, `auth.login`, `auth.logout`, `auth.refresh_reuse_detected` (A16)
- `session.create`
- `consent.grant`, `attestation.submit`, `consent.revoke`, `jobs.cancelled`, `job.cancel_observed`
- `recording.upload`, `recording.replace`, `recording.duplicate`
- `speaker.confirm`, `segment.reassign`
- `claim.dismiss`, `claim.confirm_quote`
- `document.upload`, `document.page_replace`
- `evidence.view` (snippet or page image URL issued)
- `report.generate`, `report.download`
- `session.delete_request`, `session.erased`, `retention.purged`
- `admin.*`

Audit rows store ids and metadata only, never content. Append-only is enforced by a database trigger (doc 11 §3, A13).

### B.5 Deletion [MVP]
`DELETE /sessions/{id}`:
1. → `202`.
2. Cancel the session's jobs.
3. The `session.erase` job deletes all session rows (cascade) and object prefixes (audio, documents, exports).
4. Record a `deletion_receipts` entry and show the receipt.

**Kept as a tombstone:** the session id, owner and timestamps; consent records (no content); audit entries. Provider-side copies (ElevenLabs, LLM) follow their retention terms **[A — verify per provider at HC-7]**.

### B.6 Items requiring professional legal review (HC-7) — not resolved here
- DPDP Act 2023 / DPDP Rules 2025 (notified 13 Nov 2025; most obligations phase in by ~May 2027 [V, secondary]): notice, purpose, retention, rights, and processing of the **agent's** voice and personal data.
- Lawfulness and evidential use of recordings uploaded by a party; the `agent_informed ≠ yes` policy (Q-13).
- Liability and defamation risk of "possible mismatch" labelling, and the disclaimers.
- Cross-border processing by STT/LLM providers; residency (Q-4).

## Part C — Deployment
- **local:** docker compose.
- **MVP staging/demo:** one VM (8 vCPU, 16 GB, no GPU), compose behind Caddy TLS; India region preferred [Q-4]. A single API instance, so no sticky-session concern.
- **CI/CD:** PR → lint, types, unit + integration, build. main → staging + smoke E2E. Tag → production after HC-8.
- **Observability:**
  - structlog JSON
  - OpenTelemetry spans per job
  - metrics: job queue depth, job durations per kind, provider error rates, verification status mix
  - self-hosted Sentry with PII scrubbing
- **Deferred → Later-L1:** multi-instance API with sticky WS sessions and live-latency SLOs.

## Acceptance criteria (Phase 4 exit)
- Security suite passes: ownership bypass, ticket replay, malicious PDF, image bomb, oversized and renamed files.
- Erase leaves only tombstones (test). Retention purge works on time-shifted fixtures.
- Every A.3 metric has a dated report. Targets missed are documented with mitigations; nothing is claimed beyond the reports.

## Human review
- **HC-7** (legal) before any real user.
- **HC-8** (release readiness) uses the A.3 reports.
