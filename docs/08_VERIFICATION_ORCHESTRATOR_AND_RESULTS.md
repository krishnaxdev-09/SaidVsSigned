# 08 — Verification Orchestrator, Results & Canonical Schemas

> **Version 0.2.1 — 2026-11-13 (upload-first MVP revision; HC-5 visibility note in 0.2.1).** Original: `_archive/v0.1-2026-11-13/08_…md`. Changes: `revision/CHANGELOG.md`.
> **Scope:** [MVP] batch orchestration, merge, evidence validation, versioned results, results dashboard data. [Deferred → Later-L1] live incremental behaviour (§9).
> **This document is the single source of truth** for: `Status`, `ReasonCode`, `FindingKind`, the evidence models, `EngineVerdict`, `VerificationResult`, confidence thresholds, gates, merge rules and status transitions. Other documents reference these definitions and never redefine them.

Labels: **[Accepted]** user-approved · **[Proposed]** awaiting a checkpoint · **[A]** assumption · **[Q]** open question.

## 1. Purpose
For each verifiable claim:
1. route it to the applicable engines;
2. apply uncertainty gates;
3. merge the engine verdicts into one of four statuses;
4. validate evidence against the database;
5. persist a versioned result with a clarification question;
6. publish it to the UI.

This covers P0-2 (evidence-linked) and P0-4 (uncertainty-aware). P0-1 (live) is deferred (ADR-015).

**Dependencies:**
- Claims: doc 04.
- Engines: doc 05 (contract), doc 06 (finance), doc 07 (rules, public data).
- Persistence and events: doc 11.
- UI: doc 10.

## 2. Status taxonomy [Accepted — ADR-007]
| Status | UI label | Use only when |
|---|---|---|
| `CONFLICT` | Possible mismatch | The validated evidence combinations in §4 contradict the claim, and every gate in §5 passed |
| `MATCH` | Matches the document | The claim's **primary comparison** (§6) is supported by validated evidence, and no engine reports CONFLICT or UNCLEAR |
| `UNCLEAR` | Needs clarification | Evidence exists, but the speaker is unconfirmed, confidence is low, the claim is ambiguous or vague, the outcome depends on an assumption, or the engines disagree |
| `NOT_VERIFIED` | Couldn't check | There is no document, an input is missing, no clause was found, an applicability check failed, a calculation is disabled or failed, or an engine errored |

Hard rules:
- An engine failure never becomes `MATCH`.
- No text ever says an agent is fraudulent, lying or acting illegally (§8 wording lint).
- Every non-MATCH result carries a `suggested_question` whenever one can be generated. A test enforces ≥ 95% coverage on the eval set.

## 3. Canonical enums and models

### 3.1 ReasonCode
Each code belongs to exactly one status.

| Code | Status | Producer | Meaning / clarification question template |
|---|---|---|---|
| `no_document` | NOT_VERIFIED | contract | No ready document. "Ask for the KFS / agreement and upload it." |
| `clause_not_found` | NOT_VERIFIED | contract | Nothing retrieved above `T_ret`. "Ask the agent to show where this is written." |
| `page_unreadable` | NOT_VERIFIED | contract | The relevant page failed OCR. "Upload a clearer photo of page N." |
| `missing_inputs` | NOT_VERIFIED | finance | Required inputs are absent. "Ask: {missing field}?" |
| `calc_not_converged` | NOT_VERIFIED | finance | IRR or another iterative calculation failed. Changed from UNCLEAR in v0.1 (see changelog) |
| `calculation_not_enabled` | NOT_VERIFIED | finance / contract | A needed formula is not user-enabled: MVP formulas before HC-5 approval; APR/prepayment/penal for the whole MVP (doc 06 §4a) |
| `applicability_unknown` | NOT_VERIFIED | rules | A rule fact is missing |
| `rule_not_enabled` | NOT_VERIFIED | rules | The rule is in shadow mode. **Never shown to users**; logged only |
| `entity_not_found` | NOT_VERIFIED | public_data | The lender is not in the curated demo data |
| `engine_not_applicable` | NOT_VERIFIED | orchestrator | No engine applies to this claim type |
| `not_agent_claim` | NOT_VERIFIED | orchestrator | The speaker is a confirmed customer, or the utterance is a question or hypothetical. Hidden by default |
| `engine_error` | NOT_VERIFIED | any | An engine failed after retries (doc 01 §Retry policy) |
| `low_asr_confidence` | UNCLEAR | gate | A spoken number, or the quote, is below the ASR thresholds |
| `low_ocr_confidence` | UNCLEAR | gate / contract | A clause or its number is below the OCR thresholds |
| `speaker_unconfirmed` | UNCLEAR | gate | `role_assignment != user_confirmed` |
| `vague_claim` | UNCLEAR | gate | `vagueness == vague` |
| `value_unparsed` | UNCLEAR | gate | The normaliser could not parse a spoken value |
| `condition_mismatch` | UNCLEAR | contract | The claim's condition differs from the clause's condition |
| `semantic_disagreement` | UNCLEAR | contract | NLI and the LLM judge disagree |
| `possible_rounding` | UNCLEAR | contract / finance | The difference is outside tolerance but within the rounding band (doc 06 §Tolerances) |
| `assumption_dependent` | UNCLEAR | finance / contract | The verdict flips under a recorded assumption (e.g. the GST rate, or a rate basis that wasn't stated) |
| `incomparable_units` | UNCLEAR | contract | Cannot be normalised to a common base |
| `internal_inconsistency` | UNCLEAR | finance | The agent's own numbers are mutually inconsistent and no document comparison exists. New in v0.2 |
| `public_data_only` | UNCLEAR | public_data | Only general published terms are available. Public data never yields CONFLICT |
| `awaiting_confirmation` | UNCLEAR | gate | **[Deferred → Later-L1]** The realtime result is waiting for the batch re-pass |

**Not reason codes** (kept in separate lists):
- API error codes: doc 01 §Error model.
- Log/diagnostic codes such as `quote_not_verbatim` and `extraction_failed`: doc 04.
- Document stage codes: doc 05.

### 3.2 FindingKind
`contractual` (contract engine, plus finance when any input comes from a document), `calculation_internal` (finance using spoken inputs only), `regulatory` (rules), `public` (public data).

**Mapping of v0.1 `CONFLICT_REGULATORY_NOTE`:** a rule violation is now `status=CONFLICT, finding_kind=regulatory`. It is user-visible **only if** the rule has `enabled: true`, a non-null `reviewed_by`, **and** HC-6 is approved for that rule. Otherwise the engine returns `NOT_VERIFIED(rule_not_enabled)` in **shadow mode**: the result is stored in `shadow_verdicts` and excluded from merge and UI. All MVP rules are shadow (doc 07).

### 3.3 Evidence models
```python
class QuoteAnchor(BaseModel):
    segment_id: UUID; transcript_revision: int
    start_char: int; end_char: int            # offsets within that segment's stored text

class SpokenEvidence(BaseModel):
    kind: Literal["spoken"] = "spoken"
    claim_id: UUID; quote: str; quote_anchors: list[QuoteAnchor]   # quote == join(segment.text[a:b])
    start_ms: int; end_ms: int
    speaker_role: Literal["agent","customer","unknown"]
    speaker_source: Literal["diarized","channel","user","inferred"]   # "inferred" = Later-L1 only
    role_assignment: Literal["auto","user_confirmed"]
    asr_avg_conf: float | None; asr_min_numeric_conf: float | None
    quote_confirmed_by_user: bool = False     # [Proposed, HC-3] see §5

class DocumentEvidence(BaseModel):
    kind: Literal["document"] = "document"
    document_id: UUID; document_version: int; page: int; printed_page: str | None
    clause_id: UUID; clause_text: str; bboxes: list[NormBBox]   # 0..1, top-left origin
    ocr_conf: float | None; ocr_min_numeric_conf: float | None
    text_source: Literal["text_layer","ocr"]

class CalculationEvidence(BaseModel):
    kind: Literal["calculation"] = "calculation"
    calculation_id: UUID; formula_id: str; formula_version: int
    input_sources: list[Literal["agent_said","customer_said","document","assumption"]]
    summary: str                               # templated

class RuleEvidence(BaseModel):
    kind: Literal["rule"] = "rule"
    rule_id: str; rule_version: int; instrument: str; source_url: HttpUrl
    effective_from: date; quote: str; reviewed_by: str

class PublicDataEvidence(BaseModel):
    kind: Literal["public"] = "public"
    entity_id: UUID; source_url: HttpUrl; captured_at: date; summary: str

Evidence = Annotated[SpokenEvidence | DocumentEvidence | CalculationEvidence | RuleEvidence | PublicDataEvidence,
                     Field(discriminator="kind")]
```

### 3.4 Verdicts and results
```python
class EngineVerdict(BaseModel):
    engine: Literal["contract","finance","rules","public_data"]
    status: Status; reason_code: ReasonCode | None
    finding_kind: FindingKind | None
    reason: str                                # templated text only (§8)
    evidence: list[Evidence]
    suggested_question: str | None = None
    missing: list[str] = []; assumptions: list[str] = []
    attempts: int = 1; error_class: Literal["transient","schema","input","internal"] | None = None

class StatusChange(BaseModel):
    trigger: Literal["initial","document_ready","document_replaced","speaker_confirmed",
                     "quote_confirmed","transcript_revised","claim_corrected","manual_reverify",
                     "thresholds_changed","rule_enabled"]
    previous_status: Status | None; previous_version: int | None

class VerificationResult(BaseModel):
    id: UUID; claim_id: UUID; run_id: UUID; version: int; is_current: bool
    status: Status; reason_code: ReasonCode | None; finding_kind: FindingKind | None
    headline: str; reason: str
    severity: Literal["high","medium","low","info"]
    primary_engine: Literal["contract","finance","rules","public_data"] | None
    engine_verdicts: list[EngineVerdict]       # includes sub-checks (e.g. finance "consistent")
    evidence: list[Evidence]
    suggested_question: str | None
    changed_because: StatusChange
    thresholds_version: str
    created_at: datetime

    @model_validator(mode="after")
    def _status_contract(self):
        if self.status != Status.MATCH and self.reason_code is None:
            raise ValueError("non-MATCH requires reason_code")
        if self.reason_code and REASON_STATUS[self.reason_code] != self.status:
            raise ValueError("reason_code does not belong to status")
        if self.status == Status.CONFLICT:
            validate_conflict_evidence(self)     # §4
        return self
```

## 4. CONFLICT evidence requirements
A CONFLICT is valid only with **one** of these combinations. Every gate in §5 must pass for **every** piece of spoken evidence involved, including all spoken inputs to a calculation.

| Combo | Required evidence | finding_kind | MVP |
|---|---|---|---|
| **C-DOC** | `SpokenEvidence` (agent, `user_confirmed`) + `DocumentEvidence` (page, clause_id, clause_text, bboxes) | contractual | ✅ |
| **C-CALC-DOC** | `SpokenEvidence` + `CalculationEvidence` where at least one input is `document`, + the `DocumentEvidence` for each document input | contractual | ✅ |
| **C-RULE** | `SpokenEvidence` or `DocumentEvidence` + `RuleEvidence` (enabled, reviewed, HC-6) | regulatory | ❌ shadow only |
| — | Calculation with **only spoken inputs** | calculation_internal | Never CONFLICT → `UNCLEAR(internal_inconsistency)` |
| — | Public data | public | Never CONFLICT |

**Integrity validator.** It runs before persisting, against the DB:
1. Every `quote_anchor` resolves to a stored segment of that revision, and the concatenated substring equals `quote` after NFC + whitespace normalisation.
2. `clause_text` equals the stored clause text, and the page/bboxes match the clause row.
3. The `calculation_id` exists and its inputs reference existing claim-value and doc-term rows.
4. `rule_id`/version exist and are enabled and reviewed.

Any failure sets `UNCLEAR(engine_error)` (never CONFLICT or MATCH), logs `evidence_integrity_failed` and raises an alert.

## 5. Gates and thresholds (canonical)
Thresholds are stored in `config/thresholds.yaml` with a `thresholds_version`. Initial values are **[A]** and get calibrated in Phase 4.

| Name | Initial | Applies to | Effect |
|---|---|---|---|
| `T_asr_num` | 0.85 | Min word confidence over numeric tokens in a quote, and in every spoken `Sourced` input | Below it, or `None` → CONFLICT/MATCH become `UNCLEAR(low_asr_confidence)` |
| `T_asr_avg` | 0.70 | Average word confidence over a quote (semantic claims) | Below it → `UNCLEAR(low_asr_confidence)` |
| `T_ocr` | 0.70 | Clause OCR confidence | Below it → `UNCLEAR(low_ocr_confidence)` |
| `T_ocr_num` | 0.80 | Min OCR confidence over the clause's numeric tokens | Below it → `UNCLEAR(low_ocr_confidence)` |
| `T_ret` | calibrated (E6) | Hybrid retrieval top score | Below it → `NOT_VERIFIED(clause_not_found)` |
| `T_nli_contra`, `T_nli_entail` | calibrated (E6) | NLI probabilities | See doc 05 |
| Tolerances | doc 06 §Tolerances | All numeric comparisons | The single tolerance table lives in doc 06 |

**Gate order** is applied per engine verdict before merge:
1. **Speaker gate [Accepted 2026-11-13]:** if a CONFLICT relies on a claim with `role_assignment != user_confirmed` → `UNCLEAR(speaker_unconfirmed)`. MATCH is allowed with an auto role, but the UI labels the speaker "auto-detected".
2. **Verifiability:** `speaker_role == customer` (confirmed) or `utterance_kind ∈ {question, hypothetical, opinion, customer_statement}` → `NOT_VERIFIED(not_agent_claim)`.
3. **Value gate:** a spoken value is unparsed → `UNCLEAR(value_unparsed)`.
4. **Vagueness:** `vague` → CONFLICT/MATCH become `UNCLEAR(vague_claim)`.
5. **ASR gate:** `T_asr_num` / `T_asr_avg`, as above.
   - **Missing confidence:** `None` counts as below threshold.
   - **[Proposed — decide at HC-3]:** `quote_confirmed_by_user = true` (the user played the snippet and confirmed the words) lifts the ASR gate for that quote. This is the fallback if E3 shows Scribe batch returns no usable word confidence.
6. **OCR gate:** in the contract engine (doc 05).
7. **Assumption gate:** in the finance engine (doc 06).
8. **Public-data cap:** at most `UNCLEAR(public_data_only)`.

## 6. Primary comparison per claim type [Accepted 2026-11-13 — "NOT_VERIFIED + note"]
| claim_type | Primary engine | Secondary sub-checks |
|---|---|---|
| processing_fee, other_charge, interest_rate, tenure, loan_amount, prepayment_charge, penal_charge, insurance_bundled, document_assurance, general_assurance | contract | finance (INR impact), rules (shadow) |
| emi, total_cost, net_disbursement | contract + finance on document inputs (C-CALC-DOC) | finance on spoken inputs only (`calculation_internal`) |
| lender identity (when stated) | public_data (MVP-limited) | — |
| coverage, exclusion, waiting_period, premium, claim_settlement | **[Deferred → Later-L7 insurance]** | — |

Finance sub-checks below are user-visible only after HC-5 (doc 06 §4a); before that they are `NOT_VERIFIED(calculation_not_enabled)`. If no document is ready, a self-consistent spoken calculation gives an overall status of `NOT_VERIFIED(no_document)`. The finance sub-check is shown as *"The agent's numbers are consistent with each other"*. An inconsistent one gives `UNCLEAR(internal_inconsistency)` with the question "Ask the agent to confirm the EMI in writing."

## 7. Merge algorithm
```
verdicts = [gate(v) for v in run_engines(claim)]              # failures → NOT_VERIFIED(engine_error)
visible  = [v for v in verdicts if v.reason_code != "rule_not_enabled"]   # shadow excluded
primary  = [v for v in visible if v.engine in PRIMARY[claim.claim_type]]

1. if any v.status == CONFLICT and validate_conflict_evidence(v)  → CONFLICT
      (evidence = spoken + that verdict's evidence; if several, highest severity first)
2. elif primary and all(p.status == MATCH for p in primary)
        and not any(v.status in {CONFLICT, UNCLEAR} for v in visible) → MATCH
3. elif any v.status == UNCLEAR                                  → UNCLEAR (reason by priority:
      speaker_unconfirmed > low_asr_confidence > low_ocr_confidence > value_unparsed >
      condition_mismatch > semantic_disagreement > assumption_dependent > possible_rounding >
      incomparable_units > internal_inconsistency > vague_claim > public_data_only)
4. else                                                          → NOT_VERIFIED (reason by priority:
      no_document > page_unreadable > missing_inputs > clause_not_found > calculation_not_enabled >
      calc_not_converged > applicability_unknown > entity_not_found > engine_error > engine_not_applicable)
```
If a primary engine errored, MATCH is impossible (rule 2 requires every primary verdict to be MATCH).

**Engine execution:** engines run concurrently. Timeouts: contract 30 s, finance 2 s, rules 2 s, public 3 s. Retries follow the doc 01 retry policy.

**Severity:**
- `high`: CONFLICT with an INR impact over ₹1,000 or on bundled insurance.
- `medium`: other CONFLICTs, and UNCLEAR on fee/rate/EMI/net disbursement.
- `low`: other UNCLEAR.
- `info`: MATCH and NOT_VERIFIED.

## 8. Wording rules
- Templated headlines:
  - CONFLICT: "Possible mismatch: {field}"
  - MATCH: "Matches the document: {field}"
  - UNCLEAR: "Needs clarification: {field}"
  - NOT_VERIFIED: "Couldn't check yet: {field}"
- Reason template (C-DOC): "The agent said "{quote}" ({mm:ss}). Page {page}, {section} says: "{clause_excerpt}"."
- Raw LLM rationale is never shown. It is stored for debugging only.
- **Lint test:** every template in en/hi is checked against a deny-list (fraud, lie, liar, cheat, illegal, scam, criminal, धोखा, झूठ, अवैध, …). Any match fails the build.

## 9. Status transitions and versioning
- Each run writes a new `verification_results` row with `version = previous + 1`. The old row gets `is_current = false`. Nothing is overwritten.
- Re-verification triggers (`StatusChange.trigger`):
  - document ready or replaced
  - speaker mapping confirmed
  - quote confirmed
  - transcript revised (claims re-extracted)
  - claim corrected or dismissed
  - manual re-verify
  - thresholds version changed
- Any status may move to any other. When the status changes, the UI shows "Updated because {trigger}" and the previous status.
- Re-verification for a claim is debounced to 2 s and deduplicated through the job `dedupe_key` `verify:{claim_id}` (doc 11).

**Deferred → Later-L1 (live, preserved from v0.1):**
- Emit partial results as engines finish (`partial: true`).
- Severity priority queue.
- Cancel stale runs when a claim is superseded.
- `awaiting_confirmation` until the batch re-pass.

## 10. API & events (details in doc 11)
- `GET /api/v1/sessions/{id}/results?status=` (current versions)
- `GET /api/v1/results/{id}/history`
- `POST /api/v1/sessions/{id}/reverify`
- Event `verification.updated` with payload `VerificationResult`.

## 11. Testing
- Unit:
  - Every status × gate × merge combination.
  - The `REASON_STATUS` mapping covers every ReasonCode.
  - The CONFLICT validator rejects each missing element.
  - The integrity validator catches a tampered quote or clause.
  - Shadow verdicts are never merged.
  - Engine errors never produce MATCH (property test).
  - Wording lint.
- Integration: claim → fake engines → persisted v1. Document upload → v2 with `changed_because = document_ready`.
- Eval (Phase 4): per-status confusion matrix on 200 claim–clause pairs and 30 conversations.

## 12. Acceptance criteria (MVP)
- 0 CONFLICT results without a §4 combination (property test over randomised verdicts).
- 0 CONFLICTs on claims without `user_confirmed` roles.
- 0 MATCH results when the primary engine errored or was absent.
- After calibration: CONFLICT precision ≥ 0.90 on the eval set; 0% of injected low-confidence cases become CONFLICT.
- A suggested question on ≥ 95% of non-MATCH results.

## 13. Human review
- **HC-2:** taxonomy, reason codes, the evidence combinations, the primary-comparison table and the wording templates.
- **HC-3:** the `quote_confirmed_by_user` proposal.
- **Phase 4:** the calibration report.

## 14. Common failures
- Defining enums in several modules. Import them from `app/modules/orchestrator/schemas.py` only.
- Letting a secondary-engine MATCH make the overall result MATCH.
- Applying the ASR gate only to the claim's own quote and not to the spoken inputs of a calculation.
