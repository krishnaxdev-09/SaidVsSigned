"""Canonical verification schemas — doc 08 §2–§4 (single source of truth; HC-2 approved 2026-11-14). T1.4.

Statuses, reason codes (each mapped to exactly one status), finding kinds, evidence models, engine verdicts and
versioned results, with the schema-level validators:
- every status except MATCH and CONFLICT needs a reason code, and a code must belong to its status (D-14, 2026-11-14:
  no code belongs to MATCH or CONFLICT, so both carry none). A CONFLICT is justified by its §4 evidence instead;
- a CONFLICT needs one valid evidence combination (§4). C-DOC is always allowed; C-CALC-DOC only when finance is
  user-visible (HC-5) and C-RULE only when rules are user-visible (HC-6) — both off for now (HC-2 conditions).
The database integrity validator (§4: anchors/clauses/calculations exist) and the gates/merge are T2.5.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator


class Status(StrEnum):
    CONFLICT = "CONFLICT"
    MATCH = "MATCH"
    UNCLEAR = "UNCLEAR"
    NOT_VERIFIED = "NOT_VERIFIED"


# §2 UI labels (English; HC-2 approved). Hindi is reviewed later (T4.4).
STATUS_LABELS: dict[Status, str] = {
    Status.CONFLICT: "Possible mismatch",
    Status.MATCH: "Matches the document",
    Status.UNCLEAR: "Needs clarification",
    Status.NOT_VERIFIED: "Couldn't check",
}


class ReasonCode(StrEnum):
    # NOT_VERIFIED
    NO_DOCUMENT = "no_document"
    CLAUSE_NOT_FOUND = "clause_not_found"
    PAGE_UNREADABLE = "page_unreadable"
    MISSING_INPUTS = "missing_inputs"
    CALC_NOT_CONVERGED = "calc_not_converged"
    CALCULATION_NOT_ENABLED = "calculation_not_enabled"
    APPLICABILITY_UNKNOWN = "applicability_unknown"
    RULE_NOT_ENABLED = "rule_not_enabled"
    ENTITY_NOT_FOUND = "entity_not_found"
    ENGINE_NOT_APPLICABLE = "engine_not_applicable"
    NOT_AGENT_CLAIM = "not_agent_claim"
    ENGINE_ERROR = "engine_error"
    # UNCLEAR
    LOW_ASR_CONFIDENCE = "low_asr_confidence"
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    SPEAKER_UNCONFIRMED = "speaker_unconfirmed"
    VAGUE_CLAIM = "vague_claim"
    VALUE_UNPARSED = "value_unparsed"
    CONDITION_MISMATCH = "condition_mismatch"
    SEMANTIC_DISAGREEMENT = "semantic_disagreement"
    POSSIBLE_ROUNDING = "possible_rounding"
    ASSUMPTION_DEPENDENT = "assumption_dependent"
    INCOMPARABLE_UNITS = "incomparable_units"
    INTERNAL_INCONSISTENCY = "internal_inconsistency"
    PUBLIC_DATA_ONLY = "public_data_only"
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # [Deferred → Later-L1]


_NV = Status.NOT_VERIFIED
_UN = Status.UNCLEAR
REASON_STATUS: dict[ReasonCode, Status] = {
    ReasonCode.NO_DOCUMENT: _NV, ReasonCode.CLAUSE_NOT_FOUND: _NV, ReasonCode.PAGE_UNREADABLE: _NV,
    ReasonCode.MISSING_INPUTS: _NV, ReasonCode.CALC_NOT_CONVERGED: _NV, ReasonCode.CALCULATION_NOT_ENABLED: _NV,
    ReasonCode.APPLICABILITY_UNKNOWN: _NV, ReasonCode.RULE_NOT_ENABLED: _NV, ReasonCode.ENTITY_NOT_FOUND: _NV,
    ReasonCode.ENGINE_NOT_APPLICABLE: _NV, ReasonCode.NOT_AGENT_CLAIM: _NV, ReasonCode.ENGINE_ERROR: _NV,
    ReasonCode.LOW_ASR_CONFIDENCE: _UN, ReasonCode.LOW_OCR_CONFIDENCE: _UN, ReasonCode.SPEAKER_UNCONFIRMED: _UN,
    ReasonCode.VAGUE_CLAIM: _UN, ReasonCode.VALUE_UNPARSED: _UN, ReasonCode.CONDITION_MISMATCH: _UN,
    ReasonCode.SEMANTIC_DISAGREEMENT: _UN, ReasonCode.POSSIBLE_ROUNDING: _UN, ReasonCode.ASSUMPTION_DEPENDENT: _UN,
    ReasonCode.INCOMPARABLE_UNITS: _UN, ReasonCode.INTERNAL_INCONSISTENCY: _UN, ReasonCode.PUBLIC_DATA_ONLY: _UN,
    ReasonCode.AWAITING_CONFIRMATION: _UN,
}
DEFERRED_REASON_CODES = frozenset({ReasonCode.AWAITING_CONFIRMATION})  # rejected in the MVP
SHADOW_REASON_CODES = frozenset({ReasonCode.RULE_NOT_ENABLED})  # logged only, never shown to users (§3.1)

# §7 merge priorities (used by the T2.5 merge). Exactly as doc 08 lists them.
UNCLEAR_PRIORITY: tuple[ReasonCode, ...] = (
    ReasonCode.SPEAKER_UNCONFIRMED, ReasonCode.LOW_ASR_CONFIDENCE, ReasonCode.LOW_OCR_CONFIDENCE,
    ReasonCode.VALUE_UNPARSED, ReasonCode.CONDITION_MISMATCH, ReasonCode.SEMANTIC_DISAGREEMENT,
    ReasonCode.ASSUMPTION_DEPENDENT, ReasonCode.POSSIBLE_ROUNDING, ReasonCode.INCOMPARABLE_UNITS,
    ReasonCode.INTERNAL_INCONSISTENCY, ReasonCode.VAGUE_CLAIM, ReasonCode.PUBLIC_DATA_ONLY)
NOT_VERIFIED_PRIORITY: tuple[ReasonCode, ...] = (
    ReasonCode.NO_DOCUMENT, ReasonCode.PAGE_UNREADABLE, ReasonCode.MISSING_INPUTS, ReasonCode.CLAUSE_NOT_FOUND,
    ReasonCode.CALCULATION_NOT_ENABLED, ReasonCode.CALC_NOT_CONVERGED, ReasonCode.APPLICABILITY_UNKNOWN,
    ReasonCode.ENTITY_NOT_FOUND, ReasonCode.ENGINE_ERROR, ReasonCode.ENGINE_NOT_APPLICABLE)


class FindingKind(StrEnum):  # §3.2
    CONTRACTUAL = "contractual"
    CALCULATION_INTERNAL = "calculation_internal"
    REGULATORY = "regulatory"
    PUBLIC = "public"


Engine = Literal["contract", "finance", "rules", "public_data"]
Severity = Literal["high", "medium", "low", "info"]


# ---- §3.3 evidence ---------------------------------------------------------------------------------------------------

class QuoteAnchor(BaseModel):
    segment_id: UUID
    transcript_revision: int
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> QuoteAnchor:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class NormBBox(BaseModel):
    """Normalised 0..1, top-left origin (doc 05 §Coordinates). Field names are an implementation choice [A]."""
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> NormBBox:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox must have x0 < x1 and y0 < y1")
        return self


class SpokenEvidence(BaseModel):
    kind: Literal["spoken"] = "spoken"
    claim_id: UUID
    quote: str = Field(min_length=1)
    quote_anchors: list[QuoteAnchor] = Field(min_length=1)
    start_ms: int
    end_ms: int
    speaker_role: Literal["agent", "customer", "unknown"]
    speaker_source: Literal["diarized", "channel", "user", "inferred"]
    role_assignment: Literal["auto", "user_confirmed"]
    asr_avg_conf: float | None
    asr_min_numeric_conf: float | None
    quote_confirmed_by_user: bool = False  # [Proposed, HC-3]


class DocumentEvidence(BaseModel):
    kind: Literal["document"] = "document"
    document_id: UUID
    document_version: int
    page: int = Field(ge=1)
    printed_page: str | None
    clause_id: UUID
    clause_text: str = Field(min_length=1)
    bboxes: list[NormBBox]
    ocr_conf: float | None
    ocr_min_numeric_conf: float | None
    text_source: Literal["text_layer", "ocr"]


class CalculationEvidence(BaseModel):
    kind: Literal["calculation"] = "calculation"
    calculation_id: UUID
    formula_id: str
    formula_version: int
    input_sources: list[Literal["agent_said", "customer_said", "document", "assumption"]]
    summary: str  # templated


class RuleEvidence(BaseModel):
    kind: Literal["rule"] = "rule"
    rule_id: str
    rule_version: int
    instrument: str
    source_url: HttpUrl
    effective_from: date
    quote: str
    reviewed_by: str


class PublicDataEvidence(BaseModel):
    kind: Literal["public"] = "public"
    entity_id: UUID
    source_url: HttpUrl
    captured_at: date
    summary: str


Evidence = Annotated[SpokenEvidence | DocumentEvidence | CalculationEvidence | RuleEvidence | PublicDataEvidence,
                     Field(discriminator="kind")]


# ---- §3.4 verdicts and results ---------------------------------------------------------------------------------------

# D-14 (user decision 2026-11-14, Option A): MATCH and CONFLICT carry no reason code.
NO_REASON_STATUSES = frozenset({Status.MATCH, Status.CONFLICT})


def _check_status_reason(status: Status, reason_code: ReasonCode | None) -> None:
    if status not in NO_REASON_STATUSES and reason_code is None:
        raise ValueError(f"{status.value} requires reason_code")
    if reason_code is not None and REASON_STATUS[reason_code] != status:
        raise ValueError(f"reason_code {reason_code.value} does not belong to status {status.value}")
    if reason_code in DEFERRED_REASON_CODES:
        raise ValueError(f"reason_code {reason_code.value} is deferred (Later-L1)")


class EngineVerdict(BaseModel):
    engine: Engine
    status: Status
    reason_code: ReasonCode | None
    finding_kind: FindingKind | None
    reason: str  # templated text only (§8)
    evidence: list[Evidence]
    suggested_question: str | None = None
    missing: list[str] = []
    assumptions: list[str] = []
    attempts: int = 1
    error_class: Literal["transient", "schema", "input", "internal"] | None = None

    @model_validator(mode="after")
    def _status_contract(self) -> EngineVerdict:
        _check_status_reason(self.status, self.reason_code)
        return self


class StatusChange(BaseModel):
    trigger: Literal["initial", "document_ready", "document_replaced", "speaker_confirmed", "quote_confirmed",
                     "transcript_revised", "claim_corrected", "manual_reverify", "thresholds_changed", "rule_enabled"]
    previous_status: Status | None
    previous_version: int | None


class VerificationResult(BaseModel):
    id: UUID
    claim_id: UUID
    run_id: UUID
    version: int = Field(ge=1)
    is_current: bool
    status: Status
    reason_code: ReasonCode | None
    finding_kind: FindingKind | None
    headline: str
    reason: str
    severity: Severity
    primary_engine: Engine | None
    engine_verdicts: list[EngineVerdict]
    evidence: list[Evidence]
    suggested_question: str | None
    changed_because: StatusChange
    thresholds_version: str
    created_at: datetime

    @model_validator(mode="after")
    def _status_contract(self) -> VerificationResult:
        _check_status_reason(self.status, self.reason_code)
        if self.status == Status.CONFLICT:  # justified only by validated §4 evidence (incl. the confirmed agent)
            validate_conflict_evidence(self.evidence, self.finding_kind)
        return self


# ---- §4 CONFLICT evidence combinations ------------------------------------------------------------------------------

class ConflictEvidenceError(ValueError):
    pass


def _confirmed_agent(s: SpokenEvidence) -> bool:
    return s.speaker_role == "agent" and s.role_assignment == "user_confirmed"


def _complete_document(d: DocumentEvidence) -> bool:
    return bool(d.clause_text.strip()) and bool(d.bboxes)


def validate_conflict_evidence(evidence: list[Evidence], finding_kind: FindingKind | None, *,
                               finance_user_visible: bool = False, rules_user_visible: bool = False) -> str:
    """Return the combination name (C-DOC, C-CALC-DOC, C-RULE) or raise ConflictEvidenceError.

    Every piece of spoken evidence must come from a user-confirmed agent (doc 08 §5 gate 1). Spoken-only
    calculations and public data can never be a CONFLICT. C-CALC-DOC needs finance visibility (HC-5) and C-RULE
    needs rules visibility (HC-6); both default to off (HC-2 conditions)."""
    spoken = [e for e in evidence if isinstance(e, SpokenEvidence)]
    docs = [e for e in evidence if isinstance(e, DocumentEvidence)]
    calcs = [e for e in evidence if isinstance(e, CalculationEvidence)]
    rules = [e for e in evidence if isinstance(e, RuleEvidence)]
    if any(isinstance(e, PublicDataEvidence) for e in evidence):
        raise ConflictEvidenceError("public data never yields CONFLICT")
    if spoken and not all(_confirmed_agent(s) for s in spoken):
        raise ConflictEvidenceError("CONFLICT needs every spoken quote from a user-confirmed agent")
    if rules:
        if not rules_user_visible:
            raise ConflictEvidenceError("C-RULE is shadow-only until HC-6 approves the rule")
        if finding_kind != FindingKind.REGULATORY or not (spoken or docs) or not all(r.reviewed_by for r in rules):
            raise ConflictEvidenceError("C-RULE needs spoken or document evidence, a reviewed rule and finding_kind regulatory")
        return "C-RULE"
    if calcs:
        if not any("document" in c.input_sources for c in calcs):
            raise ConflictEvidenceError("a calculation with only spoken inputs is never CONFLICT (internal_inconsistency)")
        if not finance_user_visible:
            raise ConflictEvidenceError("C-CALC-DOC stays hidden until HC-5")
        if not spoken or not docs or not all(_complete_document(d) for d in docs) \
                or finding_kind != FindingKind.CONTRACTUAL:
            raise ConflictEvidenceError("C-CALC-DOC needs spoken + calculation + the document evidence, contractual")
        return "C-CALC-DOC"
    if not spoken:
        raise ConflictEvidenceError("C-DOC needs spoken evidence from the agent")
    if not docs or not all(_complete_document(d) for d in docs):
        raise ConflictEvidenceError("C-DOC needs document evidence with page, clause_id, clause_text and bboxes")
    if finding_kind != FindingKind.CONTRACTUAL:
        raise ConflictEvidenceError("C-DOC finding_kind must be contractual")
    return "C-DOC"
