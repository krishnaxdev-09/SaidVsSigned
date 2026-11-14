"""Validated inputs only — doc 06 §3 (T3.1).

`Sourced` values can be built only by `SourcedFactory` (a private token is checked at runtime); the finance engine's
public functions accept only `LoanInputs` built from them. Passing a dict, a float, an LLM output or a hand-built
`Sourced` raises `UnvalidatedInputError`. Every input records its gates: any `gate_ok=False` input caps the verdict
at UNCLEAR with that gate's reason.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr

from app.schemas.claims import Claim, ExtractedValue

_TOKEN = object()  # module-private: only SourcedFactory sets it
GST_RATE_PCT = Decimal(18)  # A11 [A], confirmed at HC-5
Assumption = Literal["gst_rate_pct", "rate_basis_reducing"]


class UnvalidatedInputError(TypeError):
    """A finance function received a value that did not come from SourcedFactory."""


class Sourced(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: Decimal
    source: Literal["agent_said", "customer_said", "document", "assumption"]
    ref_table: Literal["claim_values", "doc_terms", "assumptions"]
    ref_id: str
    gate_ok: bool
    gate_reasons: list[str] = []
    note: str | None = None
    _token: object = PrivateAttr(default=None)

    def model_copy(self, *, update: Any = None, deep: bool = False) -> Sourced:  # type: ignore[override]
        """A copy keeps the token, so changing values through a copy would forge a validated input: refused."""
        if update:
            raise UnvalidatedInputError("Sourced values are immutable; build a new one with SourcedFactory")
        return super().model_copy(deep=deep)


def require(x: Any, name: str = "input") -> Sourced:
    if not isinstance(x, Sourced) or x._token is not _TOKEN:
        raise UnvalidatedInputError(f"{name} must come from SourcedFactory, got {type(x).__name__}")
    return x


def _make(**kw: Any) -> Sourced:
    s = Sourced(**kw)
    s._token = _TOKEN
    return s


class SourcedFactory:
    """The ONLY way to build Sourced (doc 06 §3)."""

    def __init__(self, t_asr_num: float = 0.85, t_ocr: float = 0.70, t_ocr_num: float = 0.80) -> None:
        self.t_asr_num, self.t_ocr, self.t_ocr_num = t_asr_num, t_ocr, t_ocr_num

    def from_claim_value(self, v: ExtractedValue, claim: Claim) -> Sourced:
        if v.normaliser_version is None or v.value is None:
            raise UnvalidatedInputError("claim value was not parsed by the normaliser")
        reasons = []
        if not (claim.speaker_role == "agent" and claim.role_assignment == "user_confirmed"):
            reasons.append("speaker_unconfirmed")
        if claim.vagueness == "vague":
            reasons.append("vague_claim")
        if claim.asr_min_numeric_conf is None or claim.asr_min_numeric_conf < self.t_asr_num \
                or not claim.quote_confirmed_by_user:  # ADR-019: spoken numbers need the customer's confirmation
            reasons.append("low_asr_confidence")
        return _make(value=v.value, source="customer_said" if claim.speaker_role == "customer" else "agent_said",
                     ref_table="claim_values", ref_id=str(claim.id), gate_ok=not reasons, gate_reasons=reasons,
                     note=v.raw_text)

    def from_doc_term(self, term: Any, clause: Any) -> Sourced:
        """`term`/`clause`: DB rows (mappings) of doc_terms / clauses."""
        if term["normaliser_version"] is None or term["value"] is None:
            raise UnvalidatedInputError("document term was not parsed by the normaliser")
        reasons = []
        if (clause["text_source"] or "text_layer") == "ocr" and (
                clause["ocr_conf"] is None or clause["ocr_conf"] < self.t_ocr
                or clause["ocr_min_numeric_conf"] is None or clause["ocr_min_numeric_conf"] < self.t_ocr_num):
            reasons.append("low_ocr_confidence")
        return _make(value=Decimal(term["value"]), source="document", ref_table="doc_terms", ref_id=str(term["id"]),
                     gate_ok=not reasons, gate_reasons=reasons, note=term["value_raw"])

    def assumption(self, name: Assumption, value: Decimal, reason: str) -> Sourced:
        return _make(value=value, source="assumption", ref_table="assumptions", ref_id=name, gate_ok=True,
                     note=reason)


class LoanInputs(BaseModel):
    principal: Sourced | None = None
    annual_rate_pct: Sourced | None = None
    rate_basis: Literal["reducing", "flat", "unspecified"] = "unspecified"
    tenure_months: Sourced | None = None
    processing_fee_pct: Sourced | None = None
    processing_fee_abs: Sourced | None = None
    fee_includes_gst: bool = False
    gst_rate_pct: Sourced | None = None
    other_upfront_deductions: list[Sourced] = []
    emi: Sourced | None = None
    missing: list[str] = []
    assumptions: list[str] = []

    def checked(self) -> LoanInputs:
        for name in ("principal", "annual_rate_pct", "tenure_months", "processing_fee_pct", "processing_fee_abs",
                     "gst_rate_pct", "emi"):
            v = getattr(self, name)
            if v is not None:
                require(v, name)
        for d in self.other_upfront_deductions:
            require(d, "other_upfront_deductions")
        return self

    def gate_reasons(self) -> list[str]:
        vals = [self.principal, self.annual_rate_pct, self.tenure_months, self.processing_fee_pct,
                self.processing_fee_abs, self.gst_rate_pct, self.emi, *self.other_upfront_deductions]
        return sorted({r for v in vals if v is not None for r in v.gate_reasons})
