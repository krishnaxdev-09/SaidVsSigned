# 04 — Transcript & Claim Extraction

> **Version 0.2 — 2026-11-13 (upload-first MVP revision).** Original: `_archive/v0.1-2026-11-13/04_…md`.
> **Scope:**
> - [MVP] batch claim extraction over a complete transcript revision; deterministic normalisers; quote anchoring to stored segments; dedupe; readiness tagging.
> - [MVP-limited] Hinglish few-shots, as validated by E2.
> - [Deferred] incremental live extraction (Later-L1) and insurance claim types (Later-L7, schema reserved).
>
> **Canonical here:** `Claim`, `ExtractedValue`, `ClaimType`, `UtteranceKind`, the normaliser. Status, reason codes and evidence are canonical in doc 08. Speaker fields are canonical in doc 03 §5.

## 1. Purpose
Turn transcript segments into structured, **verbatim-anchored** loan claims, with speaker, timestamps, normalised values, conditions and confidence. Questions, hypotheticals and customer statements must never be verified as agent promises.

- **Depends on:** doc 03, doc 01 (retry), `LLMProvider` (ADR-004 open).
- **Feeds:** doc 08 (and through it docs 05–07).

## 2. Prerequisites
- **HC-2 approved 2026-11-14** for this schema. The few-shots (12 English + 12 Hinglish) are reviewed by the user before T1.6 is marked complete.
- An LLM provider chosen for Phase 1 (ADR-004, open). The design works with any provider that has structured output.

## 3. LLMProvider (provider-agnostic) [Accepted: provider-agnostic]
```python
class LLMProvider(Protocol):
    name: str
    async def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel],
                         temperature: float = 0.0, max_tokens: int = 2000, timeout_s: float = 30) -> BaseModel: ...
    async def text(self, *, system: str, messages: list[dict], **kw) -> str: ...
```

Adapter requirements:
- Native JSON-schema output.
- Temperature 0.
- Errors classified per doc 01 §6. A schema failure gets **1 corrective retry**, then the window is dropped with `extraction_failed`.
- PII redaction before sending (doc 12 §B.3).
- Log the prompt version only, never raw text.

## 4. Schemas
```python
class ClaimType(StrEnum):
    PROCESSING_FEE="processing_fee"; INTEREST_RATE="interest_rate"; EMI="emi"; TENURE="tenure"
    LOAN_AMOUNT="loan_amount"; NET_DISBURSEMENT="net_disbursement"; PREPAYMENT_CHARGE="prepayment_charge"
    PENAL_CHARGE="penal_charge"; INSURANCE_BUNDLED="insurance_bundled"; OTHER_CHARGE="other_charge"
    TOTAL_COST="total_cost"; DOCUMENT_ASSURANCE="document_assurance"; GENERAL_ASSURANCE="general_assurance"
    LENDER_IDENTITY="lender_identity"
    # Reserved [Deferred → Later-L7 insurance]; extractor flag off:
    COVERAGE="coverage"; EXCLUSION="exclusion"; WAITING_PERIOD="waiting_period"; PREMIUM="premium"; CLAIM_SETTLEMENT="claim_settlement"

class UtteranceKind(StrEnum):
    PROMISE="promise"; ASSERTION="assertion"; CONDITIONAL="conditional"; HYPOTHETICAL="hypothetical"
    QUESTION="question"; OPINION="opinion"; CUSTOMER_STATEMENT="customer_statement"

class ExtractedValue(BaseModel):
    field: str                         # e.g. processing_fee, interest_rate_annual, tenure, emi, loan_amount
    raw_text: str                      # verbatim substring of the quote
    quantity: Literal["amount_inr","percent","months","years","count","boolean"]
    value: Decimal | None              # filled ONLY by the normaliser (§6)
    value_low: Decimal | None = None; value_high: Decimal | None = None
    comparator: Literal["eq","lte","gte","approx"] = "eq"
    period: Literal["annual","monthly","one_time","per_instance","unspecified"] = "unspecified"
    rate_basis: Literal["reducing","flat","unspecified"] | None = None
    rate_type: Literal["fixed","floating","unspecified"] | None = None
    of_what: str | None = None
    includes_gst: bool | None = None
    normaliser_version: str | None     # set when normaliser parsed it; None => unparsed

class Condition(BaseModel):
    text: str; kind: Literal["if_customer_does","if_eligibility","time_bound","product_variant","other"]

class QuoteAnchor(BaseModel):          # identical to doc 08 §3.3
    segment_id: UUID; transcript_revision: int; start_char: int; end_char: int

class Claim(BaseModel):
    id: UUID; session_id: UUID; transcript_revision: int
    claim_type: ClaimType; utterance_kind: UtteranceKind
    speaker_label: str | None
    speaker_role: Literal["agent","customer","unknown"]          # from speaker_assignments at read time
    speaker_source: Literal["diarized","channel","user","inferred"]
    role_assignment: Literal["auto","user_confirmed"]
    normalized_statement: str
    quote: str; quote_anchors: list[QuoteAnchor]
    start_ms: int; end_ms: int
    values: list[ExtractedValue] = []; conditions: list[Condition] = []
    vagueness: Literal["specific","approximate","vague"]
    extraction_confidence: float                                  # weak signal; never used in gates
    asr_avg_conf: float | None; asr_min_numeric_conf: float | None   # computed from words, not LLM
    quote_confirmed_by_user: bool = False                         # [Proposed, HC-3]
    readiness: Literal["checkable_now","needs_document","needs_info"]
    missing_inputs: list[str] = []
    verifiable: bool                                              # computed (§7.8)
    state: Literal["active","dismissed","superseded"] = "active"
    supersedes_claim_id: UUID | None = None                       # agent restated differently
    also_said_at: list[QuoteAnchor] = []
    prompt_version: str
```

**Changes from v0.1:**
- `quote_span` (window-relative) is replaced by `quote_anchors` (segment-relative, revision-pinned).
- Added `asr_min_numeric_conf`, `role_assignment`, `transcript_revision`, `normaliser_version`, `state` and `LENDER_IDENTITY`.
- `asr_confidence` is renamed `asr_avg_conf`.

## 5. Pipeline (job `claims.extract:{session_id}:{transcript_revision}`)
```mermaid
flowchart TD
  T[transcript revision ready] --> W[Windows: 8 segments, stride 6,<br/>+2 segments right context]
  W --> L[LLM structured → RawClaims<br/>quotes as segment_id + char offsets]
  L --> V1[Anchor validator: verbatim per segment]
  V1 --> N[Deterministic normaliser]
  N --> V2[raw_text ⊂ quote; values parsed or flagged]
  V2 --> C[Consolidation: dedupe / supersede across windows]
  C --> R[Readiness + verifiable]
  R --> DB[(claims, claim_values)]
  DB --> E[claim.created events]
  DB --> O[enqueue verify:{claim_id}]
```

## 6. Normaliser (deterministic; `claims/normalize.py`, versioned)
- **Numbers:** digits; Indian grouping (1,50,000); English and Hindi words (roman and Devanagari): ek, do, teen … sau, hazaar, lakh, crore; dedh = 1.5, dhai = 2.5, sava = +0.25, saadhe = +0.5, paune = −0.25; Devanagari digits ०–९.
- **Zero synonyms** for fee/charge claims: zero, no, nil, free, nothing, "koi nahi", "bilkul nahi" → 0.
- **Units:** %, percent, pratishat, taka → percent; k, thousand, hazaar multipliers; per month/mahina → monthly; saal/years → years (converted to months).
- **Basis:** flat → flat; reducing, diminishing, on balance → reducing. **"Fixed" is a rate type, not flat.**
- If parsing fails, set `value=None` and `normaliser_version=None`. The verification gate then gives `UNCLEAR(value_unparsed)` (doc 08 §5). **The LLM never supplies `value`**; any LLM-provided numeric field is discarded.

## 7. Steps
1. **Prompt** `prompts/claim_extract_v2.md`. The model must:
   - extract loan-term statements from any speaker and label the utterance kind;
   - copy the quote exactly and return `segment_id` + char offsets;
   - copy `raw_text` exactly, without computing;
   - mark conditions and vagueness ("very low", "best rate", "no hidden charges" → vague).

   Few-shots: 12 English + 12 Hinglish, including traps (hypotheticals, questions, customer echoes such as "zero fee na?", corrections).
2. **Anchor validator:** each anchor's `segment.text[start:end]`, joined across anchors, must equal `quote` (NFC + whitespace normalised). If not, try fuzzy re-anchoring within the cited segments (rapidfuzz ≥ 95). If that also fails, drop the claim and log `quote_not_verbatim`.
3. **Normaliser** (§6).
4. **Consolidation:**
   - Duplicate = same type, values within 1%, same speaker label, within 120 s. Keep the earliest and add the others to `also_said_at`.
   - A different value from the same speaker → new claim with `supersedes_claim_id`. Both are shown (the restatement is evidence too).
5. **Speaker fields** are read from `speaker_assignments` (doc 03 §5) at verification time, so confirming roles later needs no re-extraction.
6. **Confidence:** `asr_avg_conf` and `asr_min_numeric_conf` are computed over the anchored words.
7. **Readiness:**
   - `needs_document` when the primary engine is contract and no document is ready.
   - `needs_info` when finance inputs are missing; also fill `missing_inputs` and a question.
   - Otherwise `checkable_now`.
8. **Verifiable:** `utterance_kind ∈ {promise, assertion, conditional}` and `speaker_role ≠ customer`. An unknown or auto role is allowed, but CONFLICT is gated (doc 08).

## 8. Revisions, dismissals and idempotency
- **New transcript revision:** all claims of the old revision get `state=superseded`, a new extraction runs, and their results are re-versioned. Old rows stay for audit.
- **Re-running the same job** (same dedupe key) deletes and rewrites that revision's claims in one transaction, before any `verify` jobs are enqueued. Events are emitted after commit.
- **User actions:**
  - `PATCH /claims/{id} {state:"dismissed", user_note}` — audited; result hidden.
  - `POST /claims/{id}/confirm-quote` (proposal, HC-3) — requires that the snippet was played (client sends `played:true`, server checks a snippet fetch in the last 10 min).

## 9. API / events
- `GET /api/v1/sessions/{id}/claims?verifiable=&state=`
- `PATCH /api/v1/claims/{id}`
- `POST /api/v1/claims/{id}/confirm-quote` [Proposed]
- Events `claim.created` / `claim.updated`.

## 10. Error handling
Follows doc 01 §6:
- LLM transient errors → retry, then the job fails and `error` is emitted. Already-persisted windows are not duplicated because the job rewrites the whole revision.
- A schema failure after the corrective retry → the window is dropped and `extraction_failed` is counted. The session report notes "Part of the conversation could not be analysed (mm:ss–mm:ss)".

## 11. Testing / evaluation
- **Unit:** normaliser table (≥ 300 cases, incl. Hinglish/Devanagari), anchor validator, consolidation, readiness.
- **Property test:** for every stored claim, the quote equals the anchored text.
- **Eval:** 30 scripted conversations, with precision/recall per type, utterance-kind accuracy and value exact-match.

## 12. Acceptance criteria (Phase 1 exit)
- Agent financial claims: precision ≥ 0.85, recall ≥ 0.80 on the eval set. Report the numbers; don't assume them.
- Hypotheticals/questions misclassified as promises ≤ 5%.
- 100% verbatim anchors.
- 0 values that did not come from the normaliser.

## 13. Human review
**HC-2** (before Phase 1 code): claim types, utterance kinds, vagueness policy, few-shots.

## 14. Common failures
- The LLM converting "do percent" to 2 inside `value`. The field is discarded by design; a test checks this.
- Treating "fixed" as "flat".
- Window-relative offsets drifting.

---

## Deferred (preserved)
- **Incremental live extraction — Later-L1:** debounce 1.5 s or 3 segments; window = last 8 committed segments + open-claims summary; never extract from partials; per-claim re-extraction when a later utterance adds a condition.
- **Insurance claim types — Later-L7:** schema reserved above; requires an IRDAI research pass.
