# 14 — Architecture Decisions & Open Questions

Status values:
- **Accepted:** approved by the user on 2026-11-13.
- **Proposed:** awaiting HC-1 or a later checkpoint.
- **Open:** needs information.

## Architecture Decision Records

### ADR-001 Modular monolith + one worker — *Proposed (HC-1)*
- **Context:** small team, external APIs are the bottleneck, and the deadline favours simplicity.
- **Decision:** a single FastAPI codebase with module boundaries (doc 01). Separate API and worker processes from the same image.
- **Alternatives:** microservices (rejected: operational overhead); serverless functions (rejected: long-lived WS + audio streaming).
- **Consequences:** clear module interfaces are needed so modules can be extracted later. Docling CPU load is isolated in the worker.

### ADR-002 Postgres as DB, vector store, job queue and event bus — *Proposed (HC-1)*
- **Decision:** pgvector + FTS for hybrid retrieval, `SKIP LOCKED` jobs table, `LISTEN/NOTIFY` + `events` outbox for realtime. No Redis or Kafka in the MVP.
- **Alternatives:** Redis + arq/Celery; a dedicated vector DB (Qdrant).
- **Consequences:** fewer moving parts. NOTIFY payloads under 8 KB (we send ids only). Multi-instance WS needs sticky sessions. Revisit above about 500 concurrent live sessions.

### ADR-003 ElevenLabs Scribe behind `STTProvider` — *Accepted (provider) / Open (fallback)*
- **Decision:** `scribe_v2_realtime` for live, Scribe v2 batch with diarization for uploads and speaker correction. Audio is proxied through the backend.
- **Open:** whether a second provider is needed for Hinglish quality or outages. Decide at HC-3 using E2.

### ADR-004 LLM provider — *Open (user chose provider-agnostic, decide later)*
- **Requirements:** native JSON-schema structured output, temperature 0, good Hindi/Hinglish understanding, latency ≤2 s for an ~1.5k-token extraction call, data-retention controls, India/region availability [Q-4].
- **Decision process:** run the doc 04 claim eval and the doc 09 answer eval on 2–3 candidates during P1. Choose on precision, latency, cost and data terms. The `LLMProvider` interface makes this a config change.

### ADR-005 Embedding & reranker models — *Open (decide by P2)*
- **Requirement:** multilingual (Hinglish queries over English documents), with CPU or API inference.
- **Process:** E6 compares 2 embedding options ± reranker on Recall@5.
- **Note:** the `chunks.embedding` dimension depends on the choice. The migration is parameterised.

### ADR-006 Semantic verification = NLI cross-encoder + LLM judge agreement — *Proposed (HC-2)*
- **Decision:** a semantic CONFLICT requires both to agree. Disagreement → UNCLEAR.
- **Trade-off:** lower recall, much higher precision. This is aligned with the "avoid false accusations" requirement.

### ADR-007 Status taxonomy — *Accepted*
- `CONFLICT | MATCH | UNCLEAR | NOT_VERIFIED` with reason codes (doc 08).
- The Problem Statement's "mismatch/flag" maps to CONFLICT in UI copy.
- Insurance reuses the same taxonomy.

### ADR-008 Audio capture modes — *Accepted*
- **MVP:** in-person mic, Meet **tab capture** + mic (Chromium desktop), and recorded upload.
- **Meet Media API:** research spike (P9).
- **Headless bots:** not planned.

### ADR-009 Speaker attribution without realtime diarization — *Proposed (HC-2)*
- Channel-based where possible.
- Inferred roles cap CONFLICT to UNCLEAR until batch diarization or user confirmation.

### ADR-010 Deterministic finance, LLM only for extraction — *Accepted (P0-3)*
- APR annualisation method is decided at HC-5 after E7.

### ADR-011 Scope: loans MVP, insurance-ready — *Accepted*

### ADR-012 Noise handling — *Proposed*
- Browser WebRTC NS for the mic. No processing for Meet tab audio. Loudness normalisation only for uploads.
- RNNoise and DeepFilterNet only if E9 shows numeric-accuracy gains.

### ADR-013 Docling + OCR backend — *Open (HC-4)*
- Docling is accepted. The OCR engine (RapidOCR/EasyOCR/Tesseract) is chosen from E5.
- Tesseract is used for the per-token confidence pass unless Docling exposes confidences.

### ADR-014 Rules as curated, cited YAML with shadow mode — *Proposed (HC-6)*

## Open questions
| ID | Question | Why it matters | Owner / when |
|---|---|---|---|
| Q-1 | Do the SaidSigned PPT or UI designs exist somewhere? | Reconcile terminology and screens | User, HC-1 |
| Q-2 | LLM provider (ADR-004) | Extraction quality, cost, data terms | P1 eval |
| Q-3 | Embedding model (ADR-005) | Retrieval quality, schema dim | P2 eval |
| Q-4 | Hosting region / data residency requirement? | DPDP, provider selection | User, HC-1/HC-7 |
| Q-5 | Primary target device (Android Chrome? desktop?) | Capture modes, UI priorities | User, HC-1 |
| Q-6 | How is the agent informed: in-app read-out, verbal, or Meet chat message? | Consent design | User + legal, HC-7 |
| Q-7 | Any lender/compliance-side users planned? | Multi-tenant model, roles | User, post-MVP |
| Q-8 | Second STT provider? | Resilience, Hinglish | HC-3 |
| Q-9 | UI languages beyond en/hi? | i18n scope | User |
| Q-10 | Is malware scanning required? | Upload pipeline | User/security |
| Q-11 | Default retention period (30 days proposed)? | Privacy | User + legal, HC-7 |
| Q-12 | Team size and deadline (hackathon?) | Phase cut; fast demo path | User, HC-1 |

## Technical assumptions requiring experiments
| ID | Assumption | Experiment | Blocks |
|---|---|---|---|
| A1 | Realtime accepts PCM 16 kHz with enum `pcm_16000`, and latency p50 ≤1.5 s from India | E1 | P4 |
| A2 | Scribe handles Hinglish numbers well enough (≥95% numeric tokens) | E2 | P1, HC-3 |
| A3 | Word-level confidence is available in realtime and/or batch | E3 | P0-4 gating |
| A4 | Chrome tab-audio capture stays stable 30 min with the PWA tab hidden; video track can be stopped | E4 | P4 |
| A5 | Docling + chosen OCR reads phone photos of forms at ≥97% numeric accuracy; confidences obtainable | E5 | P2 |
| A6 | NLI + LLM-judge agreement yields semantic CONFLICT precision ≥0.9 | E6 | P2/P5 |
| A7 | APR method matches the RBI KFS illustration | E7 | P3 |
| A8 | Meet Media API requires all participants enrolled (blocks real-agent use) | E8 | P9 |
| A9 | Denoising does not reduce WER on our data | E9 | ADR-012 |
| A10 | Server VAD gating reduces ElevenLabs cost (billing by audio sent) | E1 (billing check) | ADR-012 |
| A11 | GST on processing fees = 18% | Doc check at HC-5 | P3 |
| A12 | RBI NBFC/bank lists downloadable in a parseable format | P7 spike | P7 |
| A13 | Penal charges circular (Aug 2023) content as summarised | Doc check at HC-6 | P7 |
