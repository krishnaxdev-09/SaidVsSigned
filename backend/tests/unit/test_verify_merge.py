"""T2.5: gates (doc 08 §5), merge (§7) and acceptance properties (§12), including HC-3 / ADR-019."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.verify import gates
from app.modules.verify.merge import NON_BLOCKING, Outcome, merge
from app.modules.verify.service import is_hindi, spoken_evidence
from app.schemas.claims import Claim, ExtractedValue
from app.schemas.verification import (
    REASON_STATUS,
    DocumentEvidence,
    EngineVerdict,
    FindingKind,
    NormBBox,
    QuoteAnchor,
    ReasonCode,
    Status,
)

TH = gates.Thresholds("t", 0.85, 0.70, 0.70, 0.80, 0.73)


def claim(**over: Any) -> Claim:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(), session_id=uuid.uuid4(), transcript_revision=1, claim_type="processing_fee",
        utterance_kind="assertion", speaker_label="speaker_0", speaker_role="agent", speaker_source="diarized",
        role_assignment="user_confirmed", normalized_statement="fee 1%", quote="processing fee is only 1 percent",
        quote_anchors=[QuoteAnchor(segment_id=uuid.uuid4(), transcript_revision=1, start_char=0, end_char=33)],
        start_ms=61000, end_ms=64000,
        values=[ExtractedValue(field="processing_fee", raw_text="1 percent", quantity="percent", value=Decimal(1),
                               normaliser_version="norm-1.0")],
        vagueness="specific", extraction_confidence=0.9, asr_avg_conf=0.95, asr_min_numeric_conf=0.95,
        quote_confirmed_by_user=True, readiness="needs_document", verifiable=True, prompt_version="v2")
    base.update(over)
    return Claim(**base)


def doc_ev() -> DocumentEvidence:
    return DocumentEvidence(document_id=uuid.uuid4(), document_version=1, page=1, printed_page=None,
                            clause_id=uuid.uuid4(), clause_text="Item | Details\nProcessing fee | 2% of loan amount + GST",
                            bboxes=[NormBBox(x0=0.1, y0=0.2, x1=0.5, y1=0.24)], ocr_conf=1.0, ocr_min_numeric_conf=1.0,
                            text_source="text_layer")


def v(engine: str, status: Status, code: ReasonCode | None = None, evidence: list | None = None,
      kind: FindingKind | None = FindingKind.CONTRACTUAL) -> EngineVerdict:
    return EngineVerdict(engine=engine, status=status, reason_code=code,  # type: ignore[arg-type]
                         finding_kind=kind if status in (Status.CONFLICT, Status.MATCH) else None, reason="r",
                         evidence=evidence if evidence is not None else ([doc_ev()] if engine == "contract" else []))


def run(c: Claim, *verdicts: EngineVerdict, hindi: bool = False, shadow: set[int] = frozenset()):  # type: ignore[no-untyped-def,assignment]
    outs = [Outcome(gates.apply_gates(x, c, TH, hindi=hindi), shadow=i in shadow) for i, x in enumerate(verdicts)]
    return merge(c, spoken_evidence(c), outs)


def test_conflict_with_c_doc_evidence() -> None:
    m = run(claim(), v("contract", Status.CONFLICT))
    assert (m.status, m.reason_code, m.finding_kind, m.severity) == (Status.CONFLICT, None, FindingKind.CONTRACTUAL, "medium")
    assert m.headline == "Possible mismatch: processing fee" and m.reason.startswith('The agent said "processing fee')
    assert "(01:01)" in m.reason and [e.kind for e in m.evidence] == ["spoken", "document"]


def test_match() -> None:
    m = run(claim(), v("contract", Status.MATCH))
    assert (m.status, m.severity, m.headline) == (Status.MATCH, "info", "Matches the document: processing fee")


def test_gates_in_order() -> None:
    conflict = v("contract", Status.CONFLICT)
    assert run(claim(role_assignment="auto"), conflict).reason_code == ReasonCode.SPEAKER_UNCONFIRMED
    assert run(claim(role_assignment="auto"), v("contract", Status.MATCH)).status == Status.MATCH  # MATCH ok with auto
    assert run(claim(utterance_kind="hypothetical", verifiable=False), conflict).reason_code == ReasonCode.NOT_AGENT_CLAIM
    assert run(claim(speaker_role="customer", verifiable=False), conflict).reason_code == ReasonCode.NOT_AGENT_CLAIM
    unparsed = [ExtractedValue(field="processing_fee", raw_text="1 percent", quantity="percent")]
    assert run(claim(values=unparsed), conflict).reason_code == ReasonCode.VALUE_UNPARSED
    assert run(claim(vagueness="vague", values=[]), conflict).reason_code == ReasonCode.VAGUE_CLAIM
    assert run(claim(asr_min_numeric_conf=0.5), conflict).reason_code == ReasonCode.LOW_ASR_CONFIDENCE
    assert run(claim(asr_min_numeric_conf=None), v("contract", Status.MATCH)).reason_code == ReasonCode.LOW_ASR_CONFIDENCE
    assert run(claim(values=[], asr_avg_conf=0.5), v("contract", Status.MATCH)).reason_code == ReasonCode.LOW_ASR_CONFIDENCE


def test_hc3_numeric_conflict_needs_quote_confirmation_and_never_in_hindi() -> None:
    conflict = v("contract", Status.CONFLICT)
    m = run(claim(quote_confirmed_by_user=False), conflict)
    assert m.reason_code == ReasonCode.LOW_ASR_CONFIDENCE and m.verdicts[0].missing == ["quote_confirmation"]
    gated = gates.apply_gates(conflict, claim(quote_confirmed_by_user=False), TH, hindi=False)
    assert gated.missing == ["quote_confirmation"] and gated.suggested_question.startswith("Play the clip")
    low = gates.apply_gates(conflict, claim(asr_min_numeric_conf=0.5), TH, hindi=False)
    assert low.missing == [] and gates.apply_gates(conflict, claim(), TH, hindi=True).missing == []
    assert run(claim(quote_confirmed_by_user=True), conflict, hindi=True).reason_code == ReasonCode.LOW_ASR_CONFIDENCE
    assert run(claim(quote_confirmed_by_user=False), v("contract", Status.MATCH)).status == Status.MATCH
    assert run(claim(values=[], quote_confirmed_by_user=False), conflict).status == Status.CONFLICT  # semantic claim
    assert is_hindi("hi", "hin", "fee") and not is_hindi("hinglish", "hin", "फीस")
    assert is_hindi(None, "hin", "प्रोसेसिंग फीस सिर्फ 1 प्रतिशत") and not is_hindi(None, "hin", "fee sirf 1 percent hai")


def test_unclear_and_not_verified_priorities() -> None:
    m = run(claim(), v("contract", Status.UNCLEAR, ReasonCode.POSSIBLE_ROUNDING),
            v("finance", Status.UNCLEAR, ReasonCode.ASSUMPTION_DEPENDENT))
    assert m.reason_code == ReasonCode.ASSUMPTION_DEPENDENT and m.severity == "medium"
    m = run(claim(), v("contract", Status.NOT_VERIFIED, ReasonCode.CLAUSE_NOT_FOUND),
            v("finance", Status.NOT_VERIFIED, ReasonCode.NO_DOCUMENT))
    assert (m.status, m.reason_code, m.suggested_question) == (Status.NOT_VERIFIED, ReasonCode.NO_DOCUMENT,
                                                                "Ask for the KFS / agreement and upload it.")


def test_shadow_never_merged_and_finance_does_not_block_printed_emi_match() -> None:
    m = run(claim(), v("contract", Status.MATCH), v("rules", Status.CONFLICT, kind=FindingKind.REGULATORY, evidence=[]),
            shadow={1})
    assert m.status == Status.MATCH
    emi = claim(claim_type="emi", values=[ExtractedValue(field="emi", raw_text="1 percent", quantity="amount_inr",
                                                         value=Decimal(1), normaliser_version="norm-1.0")])
    m = run(emi, v("contract", Status.MATCH), v("finance", Status.NOT_VERIFIED, ReasonCode.CALCULATION_NOT_ENABLED))
    assert m.status == Status.MATCH


def test_no_primary_engine_or_error_never_matches() -> None:
    assert run(claim(), v("finance", Status.MATCH, evidence=[])).status == Status.NOT_VERIFIED
    m = run(claim(), v("contract", Status.NOT_VERIFIED, ReasonCode.ENGINE_ERROR), v("finance", Status.MATCH, evidence=[]))
    assert (m.status, m.reason_code) == (Status.NOT_VERIFIED, ReasonCode.ENGINE_ERROR)


def test_conflict_without_valid_evidence_is_not_conflict() -> None:
    m = run(claim(), v("contract", Status.CONFLICT, evidence=[]))  # no document evidence
    assert m.status == Status.NOT_VERIFIED and m.reason_code == ReasonCode.ENGINE_ERROR
    m = run(claim(claim_type="lender_identity", values=[]), v("public_data", Status.CONFLICT, evidence=[],
                                                              kind=FindingKind.PUBLIC))
    assert m.status != Status.CONFLICT


def test_insurance_conflict_is_high_severity() -> None:
    c = claim(claim_type="insurance_bundled", values=[ExtractedValue(
        field="insurance_bundled", raw_text="1 percent", quantity="amount_inr", value=Decimal(0),
        normaliser_version="norm-1.0")])
    assert run(c, v("contract", Status.CONFLICT)).severity == "high"


# ---- §12 acceptance properties over randomised verdicts --------------------------------------------------------------

STATUSES = list(Status)
CODES = {s: [c for c, st_ in REASON_STATUS.items() if st_ == s and c != ReasonCode.AWAITING_CONFIRMATION] for s in Status}


@st.composite
def verdicts(draw: st.DrawFn) -> list[tuple[EngineVerdict, bool]]:
    out = []
    for _ in range(draw(st.integers(1, 4))):
        engine = draw(st.sampled_from(["contract", "finance", "rules", "public_data"]))
        status = draw(st.sampled_from(STATUSES))
        code = draw(st.sampled_from(CODES[status])) if CODES[status] else None
        ev = [doc_ev()] if draw(st.booleans()) else []
        kind = draw(st.sampled_from(list(FindingKind)))
        out.append((v(engine, status, code, ev, kind), draw(st.booleans())))
    return out


@settings(max_examples=400, deadline=None)
@given(verdicts(), st.sampled_from(["auto", "user_confirmed"]), st.sampled_from(["agent", "customer", "unknown"]),
       st.booleans(), st.booleans(), st.sampled_from(["processing_fee", "emi", "lender_identity", "general_assurance"]))
def test_properties(vs, role_assignment, role, confirmed, hindi, ctype) -> None:  # type: ignore[no-untyped-def]
    c = claim(role_assignment=role_assignment, speaker_role=role, quote_confirmed_by_user=confirmed, claim_type=ctype,
              verifiable=role != "customer")
    outs = [Outcome(gates.apply_gates(x, c, TH, hindi=hindi), shadow=sh) for x, sh in vs]
    m = merge(c, spoken_evidence(c), outs)
    assert len(m.verdicts) == sum(1 for o in outs if not o.shadow and o.verdict.reason_code != ReasonCode.RULE_NOT_ENABLED)
    if m.status == Status.CONFLICT:
        # 0 CONFLICTs without a §4 combination, without a confirmed agent, without a quote confirmation, or in Hindi
        assert role == "agent" and role_assignment == "user_confirmed" and confirmed and not hindi
        assert any(e.kind == "document" for e in m.evidence) and m.finding_kind == FindingKind.CONTRACTUAL
        assert any(x.status == Status.CONFLICT for x, sh in vs if not sh)
    if m.status == Status.MATCH:
        # 0 MATCH when the primary engine errored or was absent; shadow verdicts never decide
        engines = {"lender_identity": {"public_data"}, "emi": {"contract", "finance"}}.get(ctype, {"contract"})
        primary = [o for o in outs if not o.shadow and o.verdict.engine in engines]
        assert {o.verdict.engine for o in primary} >= engines  # every primary engine ran
        assert any(o.verdict.status == Status.MATCH for o in primary)
        assert all(o.verdict.status == Status.MATCH or o.verdict.reason_code in NON_BLOCKING for o in primary)
        assert not any(o.verdict.reason_code == ReasonCode.ENGINE_ERROR for o in primary)
    assert m.status in Status and (m.reason_code is None) == (m.status in (Status.MATCH, Status.CONFLICT))
