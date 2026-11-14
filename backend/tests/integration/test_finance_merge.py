"""T3.2 merge v2 with the finance flag on/off: C-CALC-DOC, printed vs computed figures, internal_inconsistency,
traces (enabled flag), and nothing finance-derived when the flag is off (doc 06 §4a, doc 08 §6–§7)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.llm.fakes import FakeEmbeddingProvider
from app.modules.contract.engine import ContractEngine
from app.modules.documents.parse import Item, ParsedDoc
from app.modules.finance.engine import CONSISTENT_NOTE, FinanceEngine
from app.modules.verify import gates
from app.modules.verify.merge import merge
from app.modules.verify.service import EngineContext, run_engines, spoken_evidence
from app.schemas.claims import ExtractedValue
from app.schemas.verification import ReasonCode, Status
from tests.conftest import scalar
from tests.integration.test_documents import drain, upload
from tests.integration.test_transcripts import ready_session
from tests.unit.test_verify_merge import claim

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
TH = gates.Thresholds("t", 0.85, 0.70, 0.70, 0.80, None, 0.5, 0.5)
AG07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()


class Ag07Parser:
    """AG07-like KFS (reducing 17.5%, 36 months, ₹10,00,000, 2% + GST, ₹18,500 insurance deducted)."""

    name = "ag07-parser"

    def __init__(self, with_emi: bool = True) -> None:
        self.with_emi = with_emi

    def parse(self, pdf_path, *, ocr, force_full_page_ocr):  # type: ignore[no-untyped-def]
        rows = [("Item", "Details"), ("Loan amount", "Rs. 10,00,000"), ("Rate of interest", "17.50% p.a. (reducing, fixed)"),
                ("Tenure", "36 months"), *([("EMI", "Rs. 35,902 per month")] if self.with_emi else []),
                ("Processing fee", "2% of loan amount + GST"),
                ("Insurance", "Rs. 18,500 (credit-shield premium, deducted from disbursement)")]
        items = [Item("#/texts/0", 1, "section_header", "Key Fact Statement", (0.1, 0.05, 0.9, 0.08))]
        for r, (a, b) in enumerate(rows):
            for c, t in enumerate((a, b)):
                items.append(Item(f"#/tables/0/r{r}c{c}", 1, "table_cell", t, (0.1 + 0.4 * c, 0.1 + 0.04 * r, 0.5 + 0.4 * c,
                                  0.13 + 0.04 * r), table={"table": "#/tables/0", "row": r, "col": c, "header": r == 0}))
        return ParsedDoc(items=items, ocr_lines=[], docling_json={}, scores={})


def money(ctype: str, value: str, **over):  # type: ignore[no-untyped-def]
    return claim(claim_type=ctype, quote=f"it is Rs {value}", **over, values=[ExtractedValue(
        field=ctype, raw_text=f"Rs {value}", quantity="amount_inr", value=Decimal(value), normaliser_version="norm-1.0")])


async def _session(client, engine, fake_store, parser):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    await upload(client, h, sid, [("ag07.pdf", AG07)])
    await drain(engine, fake_store, parser)
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))
    return uuid.UUID(sid), owner


async def _merge(engine, sid, owner, c, visible: bool):  # type: ignore[no-untyped-def]
    engines = [ContractEngine(embedder=FakeEmbeddingProvider()), FinanceEngine(visible=lambda: visible)]
    async with engine.begin() as conn:
        outs = await run_engines(engines, c, EngineContext(conn=conn, session_id=sid, owner_id=owner, thresholds=TH))
        for o in outs:
            o.verdict = gates.apply_gates(o.verdict, c, TH, hindi=False)
        return merge(c, spoken_evidence(c), outs, finance_user_visible=visible), outs


async def test_flag_off_no_finance_output(client, engine, fake_store):  # type: ignore[no-untyped-def]
    sid, owner = await _session(client, engine, fake_store, Ag07Parser(with_emi=False))
    m, outs = await _merge(engine, sid, owner, money("emi", "30000"), visible=False)
    fin = next(o.verdict for o in outs if o.verdict.engine == "finance")
    assert fin.reason_code == ReasonCode.CALCULATION_NOT_ENABLED and fin.evidence == []
    assert m.status == Status.NOT_VERIFIED and m.status != Status.CONFLICT  # no printed EMI, finance hidden
    assert not any(e.kind == "calculation" for e in m.evidence)
    assert await scalar(engine, "SELECT count(*) FROM calculations WHERE session_id = :s AND enabled", s=sid) == 0
    assert await scalar(engine, "SELECT count(*) FROM calculations WHERE session_id = :s AND NOT enabled", s=sid) == 1
    # A printed figure still matches like-for-like with the flag off.
    sid2, owner2 = await _session(client, engine, fake_store, Ag07Parser(with_emi=True))
    m, _ = await _merge(engine, sid2, owner2, money("emi", "35902"), visible=False)
    assert m.status == Status.MATCH and m.primary_engine == "contract"


async def test_flag_on_c_calc_doc(client, engine, fake_store):  # type: ignore[no-untyped-def]
    sid, owner = await _session(client, engine, fake_store, Ag07Parser(with_emi=False))
    m, _ = await _merge(engine, sid, owner, money("emi", "35902"), visible=True)
    assert m.status == Status.MATCH and m.primary_engine == "finance"  # computed EMI 35,902.xx from P, R, n
    m, _ = await _merge(engine, sid, owner, money("emi", "30000"), visible=True)
    assert m.status == Status.CONFLICT and m.severity == "high"  # ₹5,902/month over 36 months
    assert [e.kind for e in m.evidence][:2] == ["spoken", "calculation"] and any(e.kind == "document" for e in m.evidence)
    # "You get the full ₹10 lakh": net = 10,00,000 − 20,000 fee − 3,600 GST − 18,500 insurance = 9,57,900.
    m, outs = await _merge(engine, sid, owner, money("net_disbursement", "1000000"), visible=True)
    assert m.status == Status.CONFLICT
    calc = next(e for e in m.evidence if e.kind == "calculation")
    assert "957900.00" in calc.summary and "document" in calc.input_sources
    m, _ = await _merge(engine, sid, owner, money("net_disbursement", "957900"), visible=True)
    assert m.status == Status.MATCH
    # The finance verdict is still capped by the spoken gates (ADR-019: unconfirmed quote).
    m, _ = await _merge(engine, sid, owner, money("emi", "30000", quote_confirmed_by_user=False), visible=True)
    assert m.status == Status.UNCLEAR and m.reason_code == ReasonCode.LOW_ASR_CONFIDENCE
    assert await scalar(engine, "SELECT count(*) FROM calculations WHERE session_id = :s AND enabled", s=sid) >= 4


async def test_internal_consistency_without_document(client, engine, fake_store, monkeypatch):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))
    said = [money("loan_amount", "1000000"), claim(claim_type="interest_rate", values=[ExtractedValue(
        field="interest_rate", raw_text="1 percent", quantity="percent", value=Decimal("17.5"), normaliser_version="norm-1.0",
        rate_basis="reducing")]), claim(claim_type="tenure", values=[ExtractedValue(
            field="tenure", raw_text="1 percent", quantity="months", value=Decimal(36), normaliser_version="norm-1.0")])]

    async def fake_load(conn, session_id, **kw):  # type: ignore[no-untyped-def]
        return said

    monkeypatch.setattr("app.modules.claims.service.load_claims", fake_load)
    eng = FinanceEngine(visible=lambda: True)
    async with engine.begin() as conn:
        ctx = EngineContext(conn=conn, session_id=uuid.UUID(sid), owner_id=owner, thresholds=TH)
        ok = (await eng.verify(money("emi", "35902"), ctx)).verdict
        bad = (await eng.verify(money("emi", "25000"), ctx)).verdict
        hidden = (await FinanceEngine(visible=lambda: False).verify(money("emi", "25000"), ctx)).verdict
    assert ok.reason_code == ReasonCode.NO_DOCUMENT and CONSISTENT_NOTE in ok.reason
    assert bad.reason_code == ReasonCode.INTERNAL_INCONSISTENCY and bad.finding_kind.value == "calculation_internal"
    assert hidden.reason_code == ReasonCode.CALCULATION_NOT_ENABLED  # internal_inconsistency is hidden before HC-5
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM calculations WHERE session_id = :s"),
                                   {"s": uuid.UUID(sid)})).scalar() == 3
