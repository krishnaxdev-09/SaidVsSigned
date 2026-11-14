"""Merge — doc 08 §7 (T2.5): gated engine verdicts → one result status, reason, evidence and severity."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.claims import Claim
from app.schemas.verification import (
    NOT_VERIFIED_PRIORITY,
    UNCLEAR_PRIORITY,
    ConflictEvidenceError,
    DocumentEvidence,
    Engine,
    EngineVerdict,
    Evidence,
    FindingKind,
    ReasonCode,
    Severity,
    SpokenEvidence,
    Status,
    validate_conflict_evidence,
)
from app.schemas.wording import (
    CONFLICT_QUESTION,
    FIELD_LABELS,
    QUESTION_TEMPLATES,
    REASON_C_DOC,
    REASON_TEXT,
    headline,
    mm_ss,
)

# §6 primary engines per claim type (insurance claim types are reserved, Later-L7).
PRIMARY: dict[str, set[Engine]] = {
    **{t: {"contract"} for t in ("processing_fee", "other_charge", "interest_rate", "tenure", "loan_amount",
                                 "prepayment_charge", "penal_charge", "insurance_bundled", "document_assurance",
                                 "general_assurance")},
    # emi/total_cost/net_disbursement: the contract engine compares the figure the document prints (like-for-like,
    # not gated, doc 06 §4a). Finance (C-CALC-DOC, when the document only gives inputs) is a visible sub-check; it
    # returns calculation_not_enabled before HC-5, which must not block a printed-figure MATCH.
    **{t: {"contract"} for t in ("emi", "total_cost", "net_disbursement")},
    "lender_identity": {"public_data"},
}
MEDIUM_UNCLEAR_TYPES = {"processing_fee", "interest_rate", "emi", "net_disbursement"}


@dataclass
class Outcome:
    """What an engine returns: its verdict, plus the INR impact of a mismatch when known (severity only)."""
    verdict: EngineVerdict
    inr_impact: Decimal | None = None
    shadow: bool = False


@dataclass
class Merged:
    status: Status
    reason_code: ReasonCode | None
    finding_kind: FindingKind | None
    headline: str
    reason: str
    severity: Severity
    primary_engine: Engine | None
    evidence: list[Evidence]
    suggested_question: str | None
    verdicts: list[EngineVerdict] = field(default_factory=list)


def _first(codes: list[ReasonCode], priority: tuple[ReasonCode, ...]) -> ReasonCode:
    return min(codes, key=lambda c: priority.index(c) if c in priority else len(priority))


def _clause_reason(spoken: SpokenEvidence, docs: list[DocumentEvidence], section: str | None) -> str:
    d = docs[0]
    excerpt = d.clause_text.replace("\n", " ")
    excerpt = excerpt if len(excerpt) <= 220 else excerpt[:217] + "…"
    return REASON_C_DOC.format(quote=spoken.quote, mm_ss=mm_ss(spoken.start_ms), page=d.page,
                               section=section or "the document", clause_excerpt=excerpt)


def merge(claim: Claim, spoken: SpokenEvidence, outcomes: list[Outcome], *, finance_user_visible: bool = False,
          rules_user_visible: bool = False, sections: dict[str, str | None] | None = None) -> Merged:
    sections = sections or {}
    verdicts = [o.verdict for o in outcomes]
    visible = [o for o in outcomes if not o.shadow and o.verdict.reason_code != ReasonCode.RULE_NOT_ENABLED]
    primary_engines = PRIMARY.get(claim.claim_type.value, set())
    primary = [o for o in visible if o.verdict.engine in primary_engines]
    field_label = FIELD_LABELS.get(claim.claim_type.value, claim.claim_type.value.replace("_", " "))

    def docs_of(v: EngineVerdict) -> list[DocumentEvidence]:
        return [e for e in v.evidence if isinstance(e, DocumentEvidence)]

    def section_of(v: EngineVerdict) -> str | None:
        d = docs_of(v)
        return sections.get(str(d[0].clause_id)) if d else None

    # 1. CONFLICT with a valid §4 evidence combination (highest INR impact first).
    conflicts = sorted((o for o in visible if o.verdict.status == Status.CONFLICT),
                       key=lambda o: -(o.inr_impact or Decimal(0)))
    for o in conflicts:
        ev: list[Evidence] = [spoken, *o.verdict.evidence]
        try:
            validate_conflict_evidence(ev, o.verdict.finding_kind, finance_user_visible=finance_user_visible,
                                       rules_user_visible=rules_user_visible)
        except ConflictEvidenceError:
            continue
        high = claim.claim_type.value == "insurance_bundled" or (o.inr_impact is not None and o.inr_impact > 1000)
        return Merged(Status.CONFLICT, None, o.verdict.finding_kind, headline(Status.CONFLICT, field_label),
                      _clause_reason(spoken, docs_of(o.verdict), section_of(o.verdict)) if docs_of(o.verdict)
                      else o.verdict.reason, "high" if high else "medium", o.verdict.engine, ev,
                      o.verdict.suggested_question or CONFLICT_QUESTION, verdicts)
    # 2. MATCH: every primary verdict is MATCH, nothing visible is CONFLICT/UNCLEAR, and every primary engine ran.
    if primary and {o.verdict.engine for o in primary} >= primary_engines \
            and all(o.verdict.status == Status.MATCH for o in primary) \
            and not any(o.verdict.status in (Status.CONFLICT, Status.UNCLEAR) for o in visible):
        p = primary[0].verdict
        docs = [d for o in primary for d in docs_of(o.verdict)]
        return Merged(Status.MATCH, None, p.finding_kind, headline(Status.MATCH, field_label),
                      _clause_reason(spoken, docs, section_of(p)) if docs else p.reason, "info", p.engine,
                      [spoken, *[e for o in primary for e in o.verdict.evidence]], None, verdicts)
    # 3. UNCLEAR by priority.
    unclear = [o.verdict for o in visible if o.verdict.status == Status.UNCLEAR and o.verdict.reason_code]
    if unclear:
        code = _first([v.reason_code for v in unclear if v.reason_code], UNCLEAR_PRIORITY)
        v = next(v for v in unclear if v.reason_code == code)
        sev: Severity = "medium" if claim.claim_type.value in MEDIUM_UNCLEAR_TYPES else "low"
        return Merged(Status.UNCLEAR, code, v.finding_kind, headline(Status.UNCLEAR, field_label), REASON_TEXT[code],
                      sev, v.engine, [spoken, *v.evidence], v.suggested_question or _question(code, v), verdicts)
    # 4. NOT_VERIFIED by priority (a CONFLICT that failed §4 validation lands here as engine_error).
    nv = [o.verdict for o in visible if o.verdict.status == Status.NOT_VERIFIED and o.verdict.reason_code]
    codes = [v.reason_code for v in nv if v.reason_code] or [
        ReasonCode.ENGINE_ERROR if conflicts or primary else ReasonCode.ENGINE_NOT_APPLICABLE]
    code = _first(codes, NOT_VERIFIED_PRIORITY + (ReasonCode.NOT_AGENT_CLAIM,))
    v0 = next((v for v in nv if v.reason_code == code), None)
    return Merged(Status.NOT_VERIFIED, code, v0.finding_kind if v0 else None, headline(Status.NOT_VERIFIED, field_label),
                  REASON_TEXT[code], "info", v0.engine if v0 else None, [spoken, *(v0.evidence if v0 else [])],
                  (v0.suggested_question if v0 else None) or _question(code, v0), verdicts)


def _question(code: ReasonCode, v: EngineVerdict | None) -> str | None:
    t = QUESTION_TEMPLATES.get(code)
    if t is None:
        return None
    page = next((e.page for e in (v.evidence if v else []) if isinstance(e, DocumentEvidence)), None)
    if "{page}" in t:
        return t.format(page=page) if page else None
    if "{missing_field}" in t:
        return t.format(missing_field=", ".join(v.missing)) if v and v.missing else None
    return t
