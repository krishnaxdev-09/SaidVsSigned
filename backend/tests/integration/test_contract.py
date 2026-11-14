"""T2.6 contract engine against an ingested KFS (KfsParser → clauses/doc_terms/chunks) with fake LLM judge + NLI."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.llm.fakes import FakeEmbeddingProvider, FakeLLMProvider
from app.modules.contract.engine import ContractEngine
from app.modules.verify import gates
from app.modules.verify.service import EngineContext
from app.schemas.claims import Condition
from app.schemas.verification import ReasonCode, Status
from tests.conftest import scalar
from tests.integration.test_documents import KfsParser, drain, upload
from tests.integration.test_transcripts import ready_session
from tests.unit.test_verify_merge import claim

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
TH = gates.Thresholds("t", 0.85, 0.70, 0.70, 0.80, None, 0.5, 0.5)


class FakeNLI:
    name = "fake-nli"

    def __init__(self, label: str) -> None:
        self.label = label

    async def score(self, premise: str, hypothesis: str) -> dict[str, float]:
        return {"contradiction": 0.0, "entailment": 0.0, "neutral": 0.0, self.label: 0.99}


def fee(value: str, quantity: str = "percent", raw: str | None = None, **kw):  # type: ignore[no-untyped-def]
    from app.schemas.claims import ExtractedValue

    raw = raw or f"{value} percent"
    return claim(quote=f"the fee is {raw}", values=[ExtractedValue(field=kw.pop("field", "processing_fee"), raw_text=raw,
                 quantity=quantity, value=Decimal(value), normaliser_version="norm-1.0", **kw)], **(
        {"claim_type": kw.get("ctype")} if "ctype" in kw else {}))


async def _session_with_doc(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    ag07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()
    await upload(client, h, sid, [("kfs.pdf", ag07)])  # text layer → text_layer clauses (OCR gate passes)
    llm = FakeLLMProvider([{"terms": [{"clause": "C1", "field": "prepayment_charge",
                                       "value_raw": "4% of the principal outstanding", "quantity": "percent"}]}])
    await drain(engine, fake_store, KfsParser(), llm)
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))
    return uuid.UUID(sid), owner


class RefJudge:
    """Fake Gemma judge: answers with the ref of the clause containing its span (retrieval order varies)."""

    name = "ref-judge"

    def __init__(self, out: dict) -> None:
        self.out = out

    async def structured(self, *, messages, schema, **kw):  # type: ignore[no-untyped-def]
        ref = ""
        for line in messages[-1]["content"].splitlines():
            if line.startswith("C") and ": " in line and self.out.get("anchor", "\0") in line:
                ref = line.split(":", 1)[0]
        return schema.model_validate({**{k: v for k, v in self.out.items() if k != "anchor"}, "clause": ref or "C1"})


async def _verify(engine, sid, owner, c, judge=None, nli="neutral", th=TH):  # type: ignore[no-untyped-def]
    llm = RefJudge({**judge, "anchor": "Prepayment attracts"}) if judge else None
    e = ContractEngine(embedder=FakeEmbeddingProvider(), llm=llm, nli=FakeNLI(nli))
    async with engine.connect() as conn:
        return (await e.verify(c, EngineContext(conn=conn, session_id=sid, owner_id=owner, thresholds=th))).verdict


async def test_numeric_path(client, engine, fake_store):  # type: ignore[no-untyped-def]
    sid, owner = await _session_with_doc(client, engine, fake_store)
    from app.schemas.claims import ExtractedValue

    def c(ctype, raw, quantity, value, **kw):  # type: ignore[no-untyped-def]
        cond = kw.pop("conditions", [])
        return claim(claim_type=ctype, quote=f"x {raw}", conditions=cond, values=[ExtractedValue(
            field=ctype, raw_text=raw, quantity=quantity, value=Decimal(value), normaliser_version="norm-1.0", **kw)])

    v = await _verify(engine, sid, owner, c("processing_fee", "2 percent", "percent", "2"))
    assert v.status == Status.MATCH and v.assumptions == ["plus GST"] and v.evidence[0].clause_text.endswith("+ GST")
    assert (await _verify(engine, sid, owner, c("processing_fee", "1 percent", "percent", "1"))).status == Status.CONFLICT
    v = await _verify(engine, sid, owner, c("processing_fee", "2.2 percent", "percent", "2.2"))
    assert v.reason_code == ReasonCode.POSSIBLE_ROUNDING
    v = await _verify(engine, sid, owner, c("processing_fee", "Rs 20000", "amount_inr", "20000"))
    assert v.reason_code == ReasonCode.CALCULATION_NOT_ENABLED  # percent ↔ INR needs a doc 06 formula (HC-5)
    v = await _verify(engine, sid, owner, c("processing_fee", "2 percent", "percent", "2", includes_gst=True))
    assert v.reason_code == ReasonCode.ASSUMPTION_DEPENDENT
    assert (await _verify(engine, sid, owner, c("processing_fee", "no fee", "amount_inr", "0"))).status == Status.CONFLICT
    v = await _verify(engine, sid, owner, c("loan_amount", "Rs 1000000", "amount_inr", "1000000"))
    assert v.status == Status.MATCH, (v.reason_code, v.evidence)
    assert (await _verify(engine, sid, owner, c("net_disbursement", "Rs 990000", "amount_inr", "990000"))).status \
        == Status.CONFLICT
    v = await _verify(engine, sid, owner, c("prepayment_charge", "no charge", "amount_inr", "0",
                                            conditions=[Condition(text="after 12 EMIs", kind="time_bound")]))
    assert v.reason_code == ReasonCode.CONDITION_MISMATCH
    assert (await _verify(engine, sid, owner, c("prepayment_charge", "no charge", "amount_inr", "0"))).status \
        == Status.CONFLICT

    # OCR gate: the same clause read by OCR with low numeric confidence.
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE clauses SET text_source = 'ocr', ocr_conf = 0.9, ocr_min_numeric_conf = 0.5 "
                                "WHERE document_id IN (SELECT id FROM documents WHERE session_id = :s)"), {"s": sid})
    v = await _verify(engine, sid, owner, c("processing_fee", "1 percent", "percent", "1"))
    assert v.reason_code == ReasonCode.LOW_OCR_CONFIDENCE and v.suggested_question == "Upload a clearer photo of page 1."


async def test_semantic_path_needs_judge_and_nli_to_agree(client, engine, fake_store):  # type: ignore[no-untyped-def]
    sid, owner = await _session_with_doc(client, engine, fake_store)
    sem = claim(claim_type="general_assurance", values=[], quote="prepayment is free", normalized_statement=
                "Prepayment attracts a charge of 4% of the principal outstanding plus GST")
    contra = {"label": "contradicts", "clause": "C1", "span": "Prepayment attracts a charge"}
    entail = {"label": "entails", "clause": "C1", "span": "Prepayment attracts a charge"}
    v = await _verify(engine, sid, owner, sem, judge=entail, nli="entailment")
    assert v.status == Status.MATCH and "Prepayment" in v.evidence[0].clause_text
    assert (await _verify(engine, sid, owner, sem, judge=contra, nli="contradiction")).status == Status.CONFLICT
    v = await _verify(engine, sid, owner, sem, judge=contra, nli="entailment")
    assert v.reason_code == ReasonCode.SEMANTIC_DISAGREEMENT
    fake_span = {"label": "contradicts", "clause": "C1", "span": "words that are not in the clause"}
    v = await _verify(engine, sid, owner, sem, judge=fake_span, nli="contradiction")
    assert v.reason_code == ReasonCode.SEMANTIC_DISAGREEMENT  # unverifiable span → the judge's label is not used
    v = await _verify(engine, sid, owner, sem, judge={"label": "neutral", "clause": "", "span": ""}, nli="neutral")
    assert v.reason_code == ReasonCode.CLAUSE_NOT_FOUND


async def test_no_document_and_scope(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))
    v = await _verify(engine, uuid.UUID(sid), owner, fee("2"))
    assert v.reason_code == ReasonCode.NO_DOCUMENT and v.suggested_question == "Ask for the KFS / agreement and upload it."
    other_sid, _ = await _session_with_doc(client, engine, fake_store)
    assert (await _verify(engine, other_sid, owner, fee("2"))).reason_code == ReasonCode.NO_DOCUMENT  # not the owner
