"""Canonical claim schemas — doc 04 §4 (HC-2 approved 2026-11-14). T1.4.

The LLM never supplies `value`: it is filled only by the deterministic normaliser (doc 04 §6, T1.5).
Speaker fields are resolved from speaker_assignments at read time (doc 03 §5, T1.3).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.verification import QuoteAnchor


class ClaimType(StrEnum):
    PROCESSING_FEE = "processing_fee"
    INTEREST_RATE = "interest_rate"
    EMI = "emi"
    TENURE = "tenure"
    LOAN_AMOUNT = "loan_amount"
    NET_DISBURSEMENT = "net_disbursement"
    PREPAYMENT_CHARGE = "prepayment_charge"
    PENAL_CHARGE = "penal_charge"
    INSURANCE_BUNDLED = "insurance_bundled"
    OTHER_CHARGE = "other_charge"
    TOTAL_COST = "total_cost"
    DOCUMENT_ASSURANCE = "document_assurance"
    GENERAL_ASSURANCE = "general_assurance"
    LENDER_IDENTITY = "lender_identity"
    # Reserved [Deferred → Later-L7 insurance]; extractor flag off
    COVERAGE = "coverage"
    EXCLUSION = "exclusion"
    WAITING_PERIOD = "waiting_period"
    PREMIUM = "premium"
    CLAIM_SETTLEMENT = "claim_settlement"


RESERVED_CLAIM_TYPES = frozenset({ClaimType.COVERAGE, ClaimType.EXCLUSION, ClaimType.WAITING_PERIOD,
                                  ClaimType.PREMIUM, ClaimType.CLAIM_SETTLEMENT})
MVP_CLAIM_TYPES = frozenset(set(ClaimType) - RESERVED_CLAIM_TYPES)


class UtteranceKind(StrEnum):
    PROMISE = "promise"
    ASSERTION = "assertion"
    CONDITIONAL = "conditional"
    HYPOTHETICAL = "hypothetical"
    QUESTION = "question"
    OPINION = "opinion"
    CUSTOMER_STATEMENT = "customer_statement"


# doc 04 §7.8: only these kinds are verifiable (and not from a confirmed customer).
VERIFIABLE_KINDS = frozenset({UtteranceKind.PROMISE, UtteranceKind.ASSERTION, UtteranceKind.CONDITIONAL})


class ExtractedValue(BaseModel):
    field: str  # e.g. processing_fee, interest_rate_annual, tenure, emi, loan_amount
    raw_text: str = Field(min_length=1)  # verbatim substring of the quote
    quantity: Literal["amount_inr", "percent", "months", "years", "count", "boolean"]
    value: Decimal | None = None  # filled ONLY by the normaliser (§6)
    value_low: Decimal | None = None
    value_high: Decimal | None = None
    comparator: Literal["eq", "lte", "gte", "approx"] = "eq"
    period: Literal["annual", "monthly", "one_time", "per_instance", "unspecified"] = "unspecified"
    rate_basis: Literal["reducing", "flat", "unspecified"] | None = None
    rate_type: Literal["fixed", "floating", "unspecified"] | None = None
    of_what: str | None = None
    includes_gst: bool | None = None
    normaliser_version: str | None = None  # set when the normaliser parsed it; None => unparsed

    @model_validator(mode="after")
    def _value_only_from_normaliser(self) -> ExtractedValue:
        if any(v is not None for v in (self.value, self.value_low, self.value_high)) and not self.normaliser_version:
            raise ValueError("value fields may only be set by the normaliser (normaliser_version required)")
        return self


class Condition(BaseModel):
    text: str
    kind: Literal["if_customer_does", "if_eligibility", "time_bound", "product_variant", "other"]


class Claim(BaseModel):
    id: UUID
    session_id: UUID
    transcript_revision: int
    claim_type: ClaimType
    utterance_kind: UtteranceKind
    speaker_label: str | None
    speaker_role: Literal["agent", "customer", "unknown"]  # from speaker_assignments at read time
    speaker_source: Literal["diarized", "channel", "user", "inferred"]
    role_assignment: Literal["auto", "user_confirmed"]
    normalized_statement: str
    quote: str = Field(min_length=1)
    quote_anchors: list[QuoteAnchor] = Field(min_length=1)
    start_ms: int
    end_ms: int
    values: list[ExtractedValue] = []
    conditions: list[Condition] = []
    vagueness: Literal["specific", "approximate", "vague"]
    extraction_confidence: float  # weak signal; never used in gates
    asr_avg_conf: float | None  # computed from words, not LLM
    asr_min_numeric_conf: float | None
    quote_confirmed_by_user: bool = False  # [Proposed, HC-3]
    readiness: Literal["checkable_now", "needs_document", "needs_info"]
    missing_inputs: list[str] = []
    verifiable: bool
    state: Literal["active", "dismissed", "superseded"] = "active"
    supersedes_claim_id: UUID | None = None
    also_said_at: list[QuoteAnchor] = []
    prompt_version: str

    @model_validator(mode="after")
    def _invariants(self) -> Claim:
        if self.claim_type in RESERVED_CLAIM_TYPES:
            raise ValueError(f"claim_type {self.claim_type.value} is reserved (Later-L7)")
        for v in self.values:
            if v.raw_text not in self.quote:
                raise ValueError("each value's raw_text must be a verbatim substring of the quote")
        expected = self.utterance_kind in VERIFIABLE_KINDS and self.speaker_role != "customer"
        if self.verifiable != expected:
            raise ValueError("verifiable must follow doc 04 §7.8 (verifiable kind and not a customer)")
        if any(a.transcript_revision != self.transcript_revision for a in self.quote_anchors):
            raise ValueError("quote anchors must be pinned to the claim's transcript revision")
        return self
