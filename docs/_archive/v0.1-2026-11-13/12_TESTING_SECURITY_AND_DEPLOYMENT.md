# 12 — Testing, Security & Deployment

## Part A — Testing & evaluation

### Test datasets (create in P0–P1, owned by `eval/`)
1. **Synthetic loan agreements (10):** personal (fixed/floating), vehicle, gold, business-MSE, and a KFS-only one-pager. Each exists in 3 renderings: digital PDF, printed + scanned, and phone photo (good, dim and angled light). Each has a ground-truth `terms.yaml`.
   - **Traps:** "2% + GST", fee in a footnote, insurance premium deducted from disbursement, flat rate stated in small print, prepayment charge on a floating loan, APR ≫ nominal rate, a penal-interest clause.
2. **Scripted conversations (30):** 15 English, 15 Hinglish, recorded by team members playing agent and customer.
   - Variety: in-person single mic (10), Meet with separate channels (10), uploaded phone recordings (10).
   - Each script has labelled claims, the expected status per claim against a paired agreement, and **imprecision traps** ("around 10%", "very small fee", hypothetical "suppose you take 10 lakh").
3. **Claim–clause pairs (200):** for NLI and contradiction evaluation, balanced across statuses.
4. **Q&A set (150 retrieval, 60 answer, 15 unanswerable).**
5. **Noise-augmented copies** for E9 and P0-4 robustness: café/fan/traffic at 10 and 5 dB SNR, and OCR digit-swap perturbations.

No real customer data is used for development without separate consent and anonymisation.

### Test layers
| Layer | Tooling | Scope |
|---|---|---|
| Unit | pytest, hypothesis; vitest | Normalisers, formulas, merge/gates, validators, resampler, reducers |
| Integration | pytest + testcontainers (Postgres+pgvector, MinIO) | Repos, job queue, event bus, ingestion with Docling on fixtures |
| API | httpx AsyncClient + schemathesis (OpenAPI fuzz) | Auth, ownership, error codes, uploads |
| WebSocket | pytest websockets client | Ticket auth, consent gate, resume `since`, backpressure |
| Audio ingestion | Fixture WAV/MP3/M4A/WebM | Normalisation, duration, magic-byte rejection |
| Provider contract | Recorded cassettes for ElevenLabs and the LLM | Adapter parsing; no network in CI |
| E2E | Playwright with fake media devices + fake STT/LLM providers | Full journey: consent → live → doc photo → conflict card → evidence drawer |
| Eval harness (`eval/run.py`) | Real providers, run manually or nightly | Metrics below, versioned reports |

### Evaluation metrics (reported separately, never merged into one "accuracy")
| Component | Metric | MVP target [R] |
|---|---|---|
| Speech recognition | WER (en), CER (Hinglish), **numeric-token accuracy** | ≤12% WER clean en; numeric ≥95% |
| Speaker identification | Segment-level role accuracy; diarization error rate on uploads | ≥90% |
| Claim extraction | P/R per type; hypothetical/question false-promise rate; value exact-match | P≥0.85, R≥0.80, FP-promise ≤5% |
| OCR | Key numeric field accuracy per rendering | digital 100%, photo-good ≥97% |
| Evidence retrieval | Recall@5, MRR | R@5 ≥0.90 |
| Contradiction detection | Per-status confusion matrix; **CONFLICT precision**; recall | precision ≥0.90 |
| Financial calculations | Golden set exact match | 100% |
| Uncertainty handling | % of injected low-confidence cases becoming CONFLICT; UNCLEAR explanation coverage | 0%; 100% |
| Latency | utterance-end → claim card; → first verdict | p50 ≤6 s; ≤10 s |

**Reporting rule:** no accuracy claim is stated in docs, the README or a demo unless it is backed by a dated eval report in `eval/reports/` with the dataset version and config hash.

## Part B — Security, consent & privacy

### Consent [R] (legal confirmation required — HC-7)
- Customer consent: explicit checkbox, plain language (en/hi), versioned text.
- Agent awareness in live modes: read-out screen plus "Agent agreed", **or** spoken consent captured at the start of the recording (flag the timestamp).
- Meet mode: show a suggested chat message announcing the recording.
- The recording indicator is always visible. Consent can be withdrawn at any time. Withdrawal stops capture and offers deletion.
- Consent records are kept even after content deletion, as minimal proof.

### Legal topics needing professional review (do not guess)
- DPDP Act 2023 and DPDP Rules 2025 (notified 13 Nov 2025; notice/consent rules phased to ~May 2027 [V, secondary]): notice contents, purpose limitation, retention, data principal rights, whether SaidSigned is a Data Fiduciary for the **agent's** personal data (their voice).
- Lawfulness of recording a private conversation with a third party in India, and the evidential use of recordings.
- Defamation and liability risk in labelling an agent's statement a "possible mismatch".
- "Not legal or financial advice" disclaimers.
- Cross-border transfer: ElevenLabs and LLM providers may process data outside India. Data residency is a question [Q-4].

### Technical controls
| Area | Control |
|---|---|
| Transport | TLS 1.2+ everywhere, HSTS, WSS only |
| AuthN | argon2id passwords; 15-min access JWT + rotating refresh (httpOnly, Secure, SameSite=Strict cookie); WS one-time tickets |
| AuthZ | Ownership checks in every repository call; optional Postgres RLS; tested |
| Uploads | Size and page limits; magic-byte sniffing; PDF sanitising (pikepdf); image re-encode + EXIF strip; parsing only in the worker with CPU/time limits; decompression-bomb guards; optional ClamAV [Q-10] |
| Storage | SSE on buckets; private buckets; presigned URLs ≤5 min |
| Secrets | Env/secret manager; never in the repo; never logged |
| LLM data minimisation | Redact PAN, Aadhaar, account, phone and email patterns from text sent to the LLM (regex + Verhoeff check for Aadhaar); send only the necessary window/clauses; zero-retention or no-training settings where the provider offers them [A] |
| Rate limiting | Per user: uploads 20/h, QA 60/h, sessions 20/day |
| Logging | Structured; no transcript/document text in logs; ids only; PII scrubber processor |
| Audit | Append-only `audit_log` for consent, upload, view of evidence, export, delete, admin access |
| Retention | `retention_until` default 30 days [Q-11]; nightly purge job; user "Delete now" → async erasure of DB rows and objects, with a receipt |
| Frontend | Strict CSP; no third-party scripts; SW excludes API/media from cache |
| Dependencies | pip-audit / npm audit in CI; Dependabot |

## Part C — Deployment

### Environments
- **local:** docker compose (pgvector/pg16, MinIO, api, worker, vite dev).
- **staging/demo:** a single VM (8 vCPU, 16 GB, no GPU) running compose behind Caddy (auto-TLS), or managed Postgres + S3. India region preferred [Q-4].
- **prod (post-pilot):** containers on a managed platform (e.g. Cloud Run/ECS/Fly) + managed Postgres with pgvector + S3. The worker scales separately. Sticky sessions for WS by `session_id`.

### Sizing notes [A → measure]
- Docling/OCR is CPU-bound, roughly 2–8 s/page on CPU (E5). One worker with concurrency 2 suits the demo.
- API: async, I/O-bound. One instance handles more than 50 concurrent live sessions (load test in P8).

### CI/CD
- PR: lint, type-check, unit + integration tests, build images.
- main: deploy to staging, run smoke E2E.
- Tagged release: deploy to prod with an Alembic migration step.

### Observability
- structlog JSON → stdout.
- OpenTelemetry traces across audio → stt → claims → orchestrator, with a `session_id` attribute.
- Metrics: STT latency, claim latency, verdict latency, provider errors, job queue depth.
- Error tracking via self-hosted Sentry, with PII scrubbing.

## Acceptance criteria
- Security tests: ownership bypass attempts fail; WS ticket replay fails; malicious PDF (JS, embedded file) is sanitised; zip-bomb image is rejected.
- Deletion test: after `DELETE /sessions/{id}`, no rows or objects remain.
- An eval report exists for every metric before the HC-8 demo sign-off.

## Human review
- **HC-7:** consent texts, privacy notice, retention, legal memo.
- **HC-8:** eval dashboard and demo run.

## Common failures
- Logging LLM prompts with full transcripts.
- Presigned URLs cached by the service worker.
- Forgetting object-storage deletion when DB rows cascade.
