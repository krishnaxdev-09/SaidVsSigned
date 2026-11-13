# 08 — Verification Orchestrator & Results (Roadmap P2 basic, P4 live, P5 calibration)

This document is where **P0-1 (live)**, **P0-2 (evidence-linked)** and **P0-4 (uncertainty-aware)** come together.

## Purpose
- Route each claim to the applicable engines.
- Merge their verdicts into one status (`CONFLICT | MATCH | UNCLEAR | NOT_VERIFIED`).
- Enforce the evidence rules.
- Re-run verification when new information arrives (document, speaker correction, transcript revision, session facts).
- Stream results to the UI.

## Schemas
```python
class Status(StrEnum):
    CONFLICT="CONFLICT"; MATCH="MATCH"; UNCLEAR="UNCLEAR"; NOT_VERIFIED="NOT_VERIFIED"

class ReasonCode(StrEnum):
    # NOT_VERIFIED
    NO_DOCUMENT="no_document"; CLAUSE_NOT_FOUND="clause_not_found"; MISSING_INPUTS="missing_inputs"
    APPLICABILITY_UNKNOWN="applicability_unknown"; ENGINE_NOT_APPLICABLE="engine_not_applicable"
    NOT_AGENT_CLAIM="not_agent_claim"; ENGINE_ERROR="engine_error"
    # UNCLEAR
    LOW_ASR_CONFIDENCE="low_asr_confidence"; LOW_OCR_CONFIDENCE="low_ocr_confidence"
    VAGUE_CLAIM="vague_claim"; SPEAKER_UNCONFIRMED="speaker_unconfirmed"
    CONDITION_MISMATCH="condition_mismatch"; SEMANTIC_DISAGREEMENT="semantic_disagreement"
    POSSIBLE_ROUNDING="possible_rounding"; ASSUMPTION_DEPENDENT="assumption_dependent"
    INCOMPARABLE_UNITS="incomparable_units"; PUBLIC_DATA_ONLY="public_data_only"
    AWAITING_CONFIRMATION="awaiting_confirmation"

class SpokenEvidence(BaseModel):
    kind: Literal["spoken"]="spoken"; claim_id: UUID; segment_ids: list[UUID]
    quote: str; start_ms: int; end_ms: int; speaker_role: str; speaker_source: str
    asr_confidence: float | None

class DocumentEvidence(BaseModel):
    kind: Literal["document"]="document"; document_id: UUID; page: int; printed_page: str | None
    clause_id: UUID; clause_text: str; bboxes: list[BBox]; ocr_confidence: float | None
    text_source: Literal["text_layer","ocr"]

class CalculationEvidence(BaseModel):
    kind: Literal["calculation"]="calculation"; trace_id: UUID; summary: str

class RuleEvidence(BaseModel):
    kind: Literal["rule"]="rule"; rule_id: str; instrument: str; source_url: HttpUrl
    effective_from: date; quote: str

class PublicDataEvidence(BaseModel):
    kind: Literal["public"]="public"; source_url: HttpUrl; captured_at: date; summary: str

Evidence = Annotated[SpokenEvidence|DocumentEvidence|CalculationEvidence|RuleEvidence|PublicDataEvidence,
                     Field(discriminator="kind")]

class EngineVerdict(BaseModel):
    engine: Literal["contract","finance","rules","public_data"]
    status: Status; reason_code: ReasonCode | None; reason: str
    evidence: list[Evidence]; confidence: float          # engine-internal, for ranking only
    suggested_question: str | None = None
    missing: list[str] = []; assumptions: list[str] = []

class VerificationResult(BaseModel):
    id: UUID; claim_id: UUID; run_id: UUID; version: int
    status: Status; reason_code: ReasonCode | None; headline: str; reason: str
    severity: Literal["high","medium","low","info"]
    engine_verdicts: list[EngineVerdict]; evidence: list[Evidence]
    suggested_question: str | None; created_at: datetime

    @model_validator(mode="after")
    def conflict_requires_evidence(self):
        if self.status == Status.CONFLICT:
            spoken = [e for e in self.evidence if e.kind == "spoken"]
            other  = [e for e in self.evidence if e.kind in ("document","calculation","rule")]
            assert spoken and spoken[0].quote and spoken[0].start_ms is not None, "CONFLICT needs spoken quote+timestamp"
            assert other, "CONFLICT needs document/calculation/rule evidence"
            for d in other:
                if d.kind == "document": assert d.page and d.clause_id and d.clause_text
            assert self.reason.strip()
        return self
```

The orchestrator also checks evidence **integrity against the DB** before persisting:
- `quote` equals the stored transcript text over those segment ids and offsets.
- `clause_text` equals the stored clause text.
- The trace and rule ids exist.

Any failure downgrades the result to `UNCLEAR(engine_error)` and raises an alert. **No unvalidated CONFLICT is ever emitted.**

## Flow

```mermaid
flowchart TD
  T1[claim.created / claim.updated] --> Q[verify queue<br/>dedupe by claim_id]
  T2[document.ready] --> RQ[requeue claims where readiness=needs_document<br/>or any contract verdict exists]
  T3[speaker corrected / transcript revised] --> RQ2[requeue affected claims]
  T4[session facts updated] --> RQ3[requeue rules/public verdicts]
  RQ & RQ2 & RQ3 --> Q
  Q --> G0{claim.verifiable?}
  G0 -- no --> NV[NOT_VERIFIED not_agent_claim<br/>(hidden by default)]
  G0 -- yes --> R[Router: engines where applies()]
  R --> E1[contract] & E2[finance] & E3[rules] & E4[public_data]
  E1 & E2 & E3 & E4 --> M[Merger + uncertainty gates]
  M --> IV[Integrity validator]
  IV --> P[(verification_results v+1,<br/>evidence_links)]
  P --> EV[verification.updated]
```

- Engines run concurrently (`asyncio.gather`) with a per-engine timeout: contract 15 s, finance 1 s, rules 1 s, public 3 s.
- A failed or timed-out engine becomes `NOT_VERIFIED(engine_error)`. The others still count.

## Merge algorithm (P0-4)
```
gates (applied to each engine verdict before merging):
  if claim.speaker_source == "inferred" and verdict == CONFLICT: → UNCLEAR(speaker_unconfirmed)
  if verdict == CONFLICT and (claim.min_numeric_conf is None or claim.min_numeric_conf < T_asr_num (0.85)):
      → UNCLEAR(low_asr_confidence)   # realtime w/o confidences → UNCLEAR(awaiting_confirmation) until batch re-pass
  if claim.vagueness == "vague": CONFLICT→UNCLEAR(vague_claim); MATCH→UNCLEAR(vague_claim)
  public_data verdicts: cap at UNCLEAR(public_data_only)
  (contract engine already applies OCR gate; finance applies assumption gate)

merge:
  reliable = [v for v in verdicts if v.status != NOT_VERIFIED]
  if any CONFLICT in reliable → CONFLICT (evidence = union of CONFLICT verdicts' evidence + spoken)
  elif any MATCH and no UNCLEAR from contract engine → MATCH
  elif any UNCLEAR → UNCLEAR (reason = highest-priority UNCLEAR reason)
  else → NOT_VERIFIED (reason = most actionable: no_document > missing_inputs > clause_not_found > applicability_unknown)
```
**Note [R]:** an inferred-speaker MATCH stays `MATCH` (no harm in confirming), but an inferred-speaker CONFLICT is always UNCLEAR until confirmed. Confirmation comes from the batch diarization pass or a user tap.

**Severity [R]:**
- `high`: CONFLICT on fee, rate, EMI, net disbursement or bundled insurance with an INR impact over ₹1,000, or any regulatory CONFLICT.
- `medium`: other CONFLICT, or UNCLEAR on a high-impact type.
- `low`: other UNCLEAR.
- `info`: MATCH and NOT_VERIFIED.

## Wording rules (avoid false accusation)
- Headlines are templated per status:
  - CONFLICT: "Possible mismatch: processing fee"
  - UNCLEAR: "Needs clarification: interest rate"
  - NOT_VERIFIED: "Couldn't check yet: EMI"
  - MATCH: "Matches the document: tenure"
- Words such as "lied", "fraud", "illegal" or "scam" are never used. A unit test greps all templates.
- Every non-MATCH result includes a **suggested question** for the customer to ask.

## Live behaviour (P0-1)
- **Minimum viable (P4):**
  - Results are emitted as soon as any engine returns. The first emission may be finance/rules only, marked `partial: true` while the contract engine is pending.
  - When the document arrives mid-conversation, every pending claim re-runs and the UI animates the status change.
  - Re-verification produces a **new version**. Previous versions are kept for the audit trail and the "what changed" view.
- **Later enhancement:** priority queue by severity, and cancelling stale runs when a claim is superseded.

## Calibration (P5)
- Thresholds live in `config/thresholds.yaml`: `T_asr_num`, `T_ocr`, `T_ocr_num`, `T_ret`, NLI contradiction/entailment, tolerances.
- Use the eval set (doc 12) to sweep thresholds and pick the setting that maximises CONFLICT **precision ≥ 0.90** first, then recall.
- Store the chosen values with the eval report hash.
- Inject noise (ASR substitutions in numbers, OCR digit swaps) to verify that low-confidence cases never become CONFLICT.

## API & events
- `GET /api/v1/sessions/{id}/results?status=CONFLICT,UNCLEAR` (latest version per claim)
- `GET /api/v1/results/{id}/history`
- `POST /api/v1/sessions/{id}/reverify` (manual)
- `verification.updated` payload: `VerificationResult` without internal confidence numbers.

## Testing
- Unit: merge table (all status combinations × gates), the CONFLICT validator (missing quote/page → raises), the integrity validator, and wording lint.
- Integration: claim → engines (fakes) → persisted result → WS event; document arrival triggers re-verification.
- Contradiction eval: per-status confusion matrix on 200 labelled pairs plus 30 conversations.

## Acceptance criteria
- 0 CONFLICT results without spoken plus document/calculation/rule evidence (property-tested).
- CONFLICT precision ≥ 0.90 on the eval set after P5.
- Injected low-confidence cases: 0% become CONFLICT.
- Live: first verdict for a `checkable_now` claim p50 ≤ 10 s after the utterance ends.

## Human review
- **HC-2:** status taxonomy, reason codes, severity and wording templates.
- **P5 calibration report:** chosen thresholds and the precision/recall trade-off.

## Common failures
- Merging lets a public-data "UNCLEAR" mask a contract CONFLICT. Precedence handles this; test it.
- Re-verification storms when the transcript revises often. Debounce per claim (2 s).
- Showing raw LLM rationale text as the "reason". Only templated reasons plus verbatim evidence are user-facing.
