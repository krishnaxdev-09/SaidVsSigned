"""T1.4 schema registry (doc 08 §2–§4, §8; doc 04 §4). HC-2 approved 2026-11-14."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.schemas import wording
from app.schemas.claims import (
    MVP_CLAIM_TYPES,
    RESERVED_CLAIM_TYPES,
    Claim,
    ClaimType,
    ExtractedValue,
    UtteranceKind,
)
from app.schemas.verification import (
    DEFERRED_REASON_CODES,
    NOT_VERIFIED_PRIORITY,
    REASON_STATUS,
    SHADOW_REASON_CODES,
    STATUS_LABELS,
    UNCLEAR_PRIORITY,
    CalculationEvidence,
    ConflictEvidenceError,
    DocumentEvidence,
    EngineVerdict,
    FindingKind,
    NormBBox,
    PublicDataEvidence,
    QuoteAnchor,
    ReasonCode,
    RuleEvidence,
    SpokenEvidence,
    Status,
    VerificationResult,
    validate_conflict_evidence,
)

SEG = uuid.uuid4()


# ---- builders --------------------------------------------------------------------------------------------------------

def spoken(role: str = "agent", assignment: str = "user_confirmed") -> SpokenEvidence:
    return SpokenEvidence(claim_id=uuid.uuid4(), quote="processing fee is only 1%",
                          quote_anchors=[QuoteAnchor(segment_id=SEG, transcript_revision=1, start_char=4, end_char=29)],
                          start_ms=10_160, end_ms=12_000, speaker_role=role, speaker_source="diarized",  # type: ignore[arg-type]
                          role_assignment=assignment, asr_avg_conf=0.99, asr_min_numeric_conf=0.99)  # type: ignore[arg-type]


def document(bboxes: bool = True, clause_text: str = "Processing fee: 2% of loan amount + GST") -> DocumentEvidence:
    return DocumentEvidence(document_id=uuid.uuid4(), document_version=1, page=1, printed_page="1",
                            clause_id=uuid.uuid4(), clause_text=clause_text,
                            bboxes=[NormBBox(x0=0.1, y0=0.2, x1=0.9, y1=0.25)] if bboxes else [],
                            ocr_conf=1.0, ocr_min_numeric_conf=1.0, text_source="text_layer")


def calculation(*sources: str) -> CalculationEvidence:
    return CalculationEvidence(calculation_id=uuid.uuid4(), formula_id="emi_reducing", formula_version=1,
                               input_sources=list(sources), summary="EMI from the document's rate and tenure")  # type: ignore[arg-type]


def rule(reviewed_by: str = "Reviewer") -> RuleEvidence:
    return RuleEvidence(rule_id="rbi-kfs-1", rule_version=1, instrument="RBI KFS circular",
                        source_url="https://rbi.org.in/x", effective_from=date(2024, 10, 1), quote="q",  # type: ignore[arg-type]
                        reviewed_by=reviewed_by)


def public() -> PublicDataEvidence:
    return PublicDataEvidence(entity_id=uuid.uuid4(), source_url="https://example.org", captured_at=date(2026, 1, 1),  # type: ignore[arg-type]
                              summary="s")


def result(status: Status, reason: ReasonCode | None, evidence: list[Any], kind: FindingKind | None) -> VerificationResult:
    return VerificationResult(
        id=uuid.uuid4(), claim_id=uuid.uuid4(), run_id=uuid.uuid4(), version=1, is_current=True, status=status,
        reason_code=reason, finding_kind=kind, headline=wording.headline(status, "processing fee"), reason="r",
        severity="medium", primary_engine="contract", engine_verdicts=[], evidence=evidence, suggested_question=None,
        changed_because={"trigger": "initial", "previous_status": None, "previous_version": None},  # type: ignore[arg-type]
        thresholds_version="0.2.2-initial", created_at=datetime.now(UTC))


# ---- REASON_STATUS ---------------------------------------------------------------------------------------------------

def test_reason_status_covers_every_code_once() -> None:
    assert set(REASON_STATUS) == set(ReasonCode) and len(ReasonCode) == 25
    by = {s: {c for c, st_ in REASON_STATUS.items() if st_ == s} for s in Status}
    assert len(by[Status.NOT_VERIFIED]) == 12 and len(by[Status.UNCLEAR]) == 13
    assert not by[Status.CONFLICT] and not by[Status.MATCH]  # CONFLICT/MATCH carry no reason code
    assert DEFERRED_REASON_CODES == {ReasonCode.AWAITING_CONFIRMATION}  # 24 MVP codes + 1 deferred (Later-L1)


def test_merge_priorities_are_consistent() -> None:
    for prio, status in ((UNCLEAR_PRIORITY, Status.UNCLEAR), (NOT_VERIFIED_PRIORITY, Status.NOT_VERIFIED)):
        assert len(set(prio)) == len(prio)
        assert all(REASON_STATUS[c] == status for c in prio)
        assert not set(prio) & (DEFERRED_REASON_CODES | SHADOW_REASON_CODES)
    assert UNCLEAR_PRIORITY[0] == ReasonCode.SPEAKER_UNCONFIRMED  # speaker gate wins (doc 08 §7)


def test_every_status_has_a_label() -> None:
    assert set(STATUS_LABELS) == set(Status) and STATUS_LABELS[Status.CONFLICT] == "Possible mismatch"


# ---- status / reason contract ---------------------------------------------------------------------------------------

def test_match_has_no_reason_code() -> None:
    assert result(Status.MATCH, None, [spoken(), document()], FindingKind.CONTRACTUAL).status == Status.MATCH
    with pytest.raises(ValidationError, match="does not belong"):
        result(Status.MATCH, ReasonCode.NO_DOCUMENT, [], None)


@pytest.mark.parametrize("status", [Status.UNCLEAR, Status.NOT_VERIFIED])
def test_unclear_and_not_verified_require_reason_code(status: Status) -> None:
    """D-14: every status except MATCH and CONFLICT needs a reason code."""
    with pytest.raises(ValidationError, match="requires reason_code"):
        result(status, None, [spoken(), document()], FindingKind.CONTRACTUAL)


def test_reason_must_belong_to_status() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        result(Status.UNCLEAR, ReasonCode.NO_DOCUMENT, [], None)
    with pytest.raises(ValidationError, match="does not belong"):
        EngineVerdict(engine="contract", status=Status.NOT_VERIFIED, reason_code=ReasonCode.VAGUE_CLAIM,
                      finding_kind=None, reason="r", evidence=[])


def test_deferred_code_rejected() -> None:
    with pytest.raises(ValidationError, match="deferred"):
        result(Status.UNCLEAR, ReasonCode.AWAITING_CONFIRMATION, [], None)


# ---- CONFLICT evidence (§4) -----------------------------------------------------------------------------------------

def test_valid_c_doc() -> None:
    assert validate_conflict_evidence([spoken(), document()], FindingKind.CONTRACTUAL) == "C-DOC"


def test_conflict_result_with_valid_c_doc_evidence() -> None:
    """D-14 (Option A): CONFLICT carries no reason code and is justified by validated §4 evidence."""
    r = result(Status.CONFLICT, None, [spoken(), document()], FindingKind.CONTRACTUAL)
    assert r.status == Status.CONFLICT and r.reason_code is None
    assert r.headline == "Possible mismatch: processing fee" and STATUS_LABELS[r.status] == "Possible mismatch"


def test_conflict_result_rejects_any_reason_code() -> None:
    for code in ReasonCode:
        with pytest.raises(ValidationError, match="does not belong|deferred"):
            result(Status.CONFLICT, code, [spoken(), document()], FindingKind.CONTRACTUAL)


@pytest.mark.parametrize(("evidence", "kind"), [
    (lambda: [spoken(assignment="auto"), document()], FindingKind.CONTRACTUAL),  # unconfirmed agent
    (lambda: [spoken(role="customer"), document()], FindingKind.CONTRACTUAL),
    (lambda: [spoken()], FindingKind.CONTRACTUAL),                                # no document
    (lambda: [document()], FindingKind.CONTRACTUAL),                              # no spoken quote
    (lambda: [], None),
    (lambda: [spoken(), calculation("agent_said", "document"), document()], FindingKind.CONTRACTUAL),  # HC-5
    (lambda: [spoken(), rule()], FindingKind.REGULATORY),                         # HC-6
])
def test_conflict_result_requires_valid_evidence(evidence, kind) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        result(Status.CONFLICT, None, evidence(), kind)


@pytest.mark.parametrize(("evidence", "kind", "why"), [
    (lambda: [document()], FindingKind.CONTRACTUAL, "spoken"),
    (lambda: [spoken(assignment="auto"), document()], FindingKind.CONTRACTUAL, "user-confirmed"),
    (lambda: [spoken(role="customer"), document()], FindingKind.CONTRACTUAL, "user-confirmed"),
    (lambda: [spoken(role="unknown"), document()], FindingKind.CONTRACTUAL, "user-confirmed"),
    (lambda: [spoken()], FindingKind.CONTRACTUAL, "document evidence"),
    (lambda: [spoken(), document(bboxes=False)], FindingKind.CONTRACTUAL, "document evidence"),
    (lambda: [spoken(), document(clause_text=" ")], FindingKind.CONTRACTUAL, "document evidence"),
    (lambda: [spoken(), document()], FindingKind.REGULATORY, "contractual"),
    (lambda: [spoken(), document()], None, "contractual"),
    (lambda: [spoken(), document(), public()], FindingKind.CONTRACTUAL, "public"),
    (lambda: [spoken(), calculation("agent_said")], FindingKind.CALCULATION_INTERNAL, "only spoken"),
])
def test_conflict_rejects_each_missing_element(evidence, kind, why) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConflictEvidenceError, match=why):
        validate_conflict_evidence(evidence(), kind)


def test_c_calc_doc_hidden_until_hc5() -> None:
    ev = [spoken(), calculation("agent_said", "document"), document()]
    with pytest.raises(ConflictEvidenceError, match="HC-5"):
        validate_conflict_evidence(ev, FindingKind.CONTRACTUAL)
    assert validate_conflict_evidence(ev, FindingKind.CONTRACTUAL, finance_user_visible=True) == "C-CALC-DOC"
    with pytest.raises(ConflictEvidenceError, match="only spoken"):  # even with finance visible
        validate_conflict_evidence([spoken(), calculation("agent_said"), document()], FindingKind.CONTRACTUAL,
                                   finance_user_visible=True)


def test_c_rule_shadow_until_hc6() -> None:
    ev = [spoken(), rule()]
    with pytest.raises(ConflictEvidenceError, match="HC-6"):
        validate_conflict_evidence(ev, FindingKind.REGULATORY)
    assert validate_conflict_evidence(ev, FindingKind.REGULATORY, rules_user_visible=True) == "C-RULE"
    with pytest.raises(ConflictEvidenceError):
        validate_conflict_evidence([spoken(), rule(reviewed_by="")], FindingKind.REGULATORY, rules_user_visible=True)


_PIECES = {"agent_ok": lambda: spoken(), "agent_auto": lambda: spoken(assignment="auto"),
           "customer": lambda: spoken(role="customer"), "doc": lambda: document(),
           "doc_nobbox": lambda: document(bboxes=False), "calc_spoken": lambda: calculation("agent_said"),
           "calc_doc": lambda: calculation("document"), "rule": lambda: rule(), "public": lambda: public()}


@settings(max_examples=300, deadline=None)
@given(names=st.lists(st.sampled_from(sorted(_PIECES)), max_size=5),
       kind=st.sampled_from([*FindingKind, None]))
def test_property_conflict_result_iff_valid_evidence(names: list[str], kind: FindingKind | None) -> None:
    """A CONFLICT *result* can be created exactly when the §4 validator accepts its evidence."""
    ev = [_PIECES[n]() for n in names]
    try:
        validate_conflict_evidence(ev, kind)
        valid = True
    except ConflictEvidenceError:
        valid = False
    try:
        result(Status.CONFLICT, None, ev, kind)
        created = True
    except ValidationError:
        created = False
    assert created == valid


@settings(max_examples=300, deadline=None)
@given(names=st.lists(st.sampled_from(sorted(_PIECES)), max_size=5),
       kind=st.sampled_from([*FindingKind, None]))
def test_property_no_conflict_without_a_valid_combination(names: list[str], kind: FindingKind | None) -> None:
    """doc 08 §12: 0 CONFLICT results without a §4 combination, and 0 without a user-confirmed agent."""
    ev = [_PIECES[n]() for n in names]
    try:
        combo = validate_conflict_evidence(ev, kind)  # MVP defaults: finance and rules not user-visible
    except ConflictEvidenceError:
        return
    assert combo == "C-DOC"
    spoken_ev = [e for e in ev if isinstance(e, SpokenEvidence)]
    docs = [e for e in ev if isinstance(e, DocumentEvidence)]
    assert spoken_ev and all(s.speaker_role == "agent" and s.role_assignment == "user_confirmed" for s in spoken_ev)
    assert docs and all(d.bboxes for d in docs) and kind == FindingKind.CONTRACTUAL
    assert not any(isinstance(e, CalculationEvidence | RuleEvidence | PublicDataEvidence) for e in ev)


def test_evidence_round_trips_through_json() -> None:
    r = result(Status.NOT_VERIFIED, ReasonCode.NO_DOCUMENT, [spoken()], None)
    again = VerificationResult.model_validate_json(r.model_dump_json())
    assert isinstance(again.evidence[0], SpokenEvidence) and again == r


@pytest.mark.parametrize("box", [dict(x0=0.5, y0=0.1, x1=0.4, y1=0.2), dict(x0=0.1, y0=0.1, x1=1.2, y1=0.2)])
def test_bbox_is_normalised_and_ordered(box: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        NormBBox(**box)


def test_quote_anchor_ordering() -> None:
    with pytest.raises(ValidationError):
        QuoteAnchor(segment_id=SEG, transcript_revision=1, start_char=5, end_char=5)


# ---- claims (doc 04 §4) -------------------------------------------------------------------------------------------

def test_claim_type_and_kind_inventory() -> None:
    assert len(MVP_CLAIM_TYPES) == 14 and len(RESERVED_CLAIM_TYPES) == 5 and len(UtteranceKind) == 7


def _claim(**over: Any) -> Claim:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(), session_id=uuid.uuid4(), transcript_revision=1, claim_type=ClaimType.PROCESSING_FEE,
        utterance_kind=UtteranceKind.ASSERTION, speaker_label="speaker_0", speaker_role="agent",
        speaker_source="diarized", role_assignment="auto", normalized_statement="Processing fee is 1%",
        quote="The processing fee is only 1%",
        quote_anchors=[QuoteAnchor(segment_id=SEG, transcript_revision=1, start_char=0, end_char=29)],
        start_ms=10_160, end_ms=12_000,
        values=[ExtractedValue(field="processing_fee", raw_text="1%", quantity="percent")],
        vagueness="specific", extraction_confidence=0.9, asr_avg_conf=0.99, asr_min_numeric_conf=0.99,
        readiness="needs_document", verifiable=True, prompt_version="claim_extract_v2")
    base.update(over)
    return Claim(**base)


def test_valid_claim() -> None:
    assert _claim().verifiable


def test_llm_cannot_supply_values() -> None:
    with pytest.raises(ValidationError, match="normaliser"):
        ExtractedValue(field="processing_fee", raw_text="do percent", quantity="percent", value=Decimal(2))
    ok = ExtractedValue(field="processing_fee", raw_text="1%", quantity="percent", value=Decimal(1),
                        normaliser_version="norm-1")
    assert ok.value == 1


@pytest.mark.parametrize(("over", "why"), [
    (dict(values=[ExtractedValue(field="f", raw_text="2%", quantity="percent")]), "verbatim"),
    (dict(claim_type=ClaimType.PREMIUM), "reserved"),
    (dict(utterance_kind=UtteranceKind.QUESTION), "verifiable"),
    (dict(speaker_role="customer"), "verifiable"),
    (dict(quote_anchors=[QuoteAnchor(segment_id=SEG, transcript_revision=2, start_char=0, end_char=5)]), "pinned"),
])
def test_claim_invariants(over: dict[str, Any], why: str) -> None:
    with pytest.raises(ValidationError, match=why):
        _claim(**over)


def test_non_verifiable_kinds() -> None:
    assert _claim(utterance_kind=UtteranceKind.HYPOTHETICAL, verifiable=False).verifiable is False


# ---- wording (§8) ----------------------------------------------------------------------------------------------------

def test_templates_pass_the_deny_list() -> None:
    texts = [*wording.HEADLINES.values(), wording.REASON_C_DOC, *wording.QUESTION_TEMPLATES.values(),
             *STATUS_LABELS.values()]
    assert all(not wording.wording_violations(t) for t in texts)


@pytest.mark.parametrize("bad", ["The agent is a liar", "This is FRAUD", "illegal charge", "यह धोखा है", "झूठ"])
def test_deny_list_catches(bad: str) -> None:
    assert wording.wording_violations(bad)


@pytest.mark.parametrize("ok", ["Fee applies after 6 EMIs", "I believe the rate is fixed", "client name"])
def test_deny_list_no_false_positive(ok: str) -> None:
    assert not wording.wording_violations(ok)


def test_headline_template() -> None:
    assert wording.headline(Status.UNCLEAR, "EMI") == "Needs clarification: EMI"
