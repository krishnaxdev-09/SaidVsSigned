"""T3.4 Ask flow through the API + qa.answer job with a fake Gemma: routes, validator retry/fallback, events, scope."""

from __future__ import annotations

import re
import uuid

import pytest

from app.llm.fakes import FakeEmbeddingProvider
from app.modules.rag.ask import FALLBACK, NOT_AVAILABLE, NOT_IN_DOCUMENT, OUT_OF_SCOPE_TEXT
from app.modules.rag.qa import make_qa_handler
from app.worker.runner import Registry, Worker
from tests.conftest import register_and_login, scalar
from tests.integration.test_documents import KfsParser, drain, pdf_bytes, upload
from tests.integration.test_transcripts import ready_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class ScriptedLLM:
    """Answers with the next scripted reply; the ref of the chunk containing `anchor` is filled in."""

    name = "scripted"

    def __init__(self, replies: list[dict]) -> None:
        self.replies, self.calls = replies, 0

    async def structured(self, *, messages, schema, **kw):  # type: ignore[no-untyped-def]
        self.calls += 1
        r = self.replies.pop(0)
        ctx = messages[0]["content"]
        for s in r.get("sentences", []):
            for c in s["citations"]:
                c["chunk"] = next((ln.split(" ", 1)[0] for ln in ctx.split("\n\n")
                                   if re.match(r"C\d+ \(page", ln) and c.get("anchor", "\0") in ln), "C1")
                c.pop("anchor", None)
        return schema.model_validate(r)


async def ask(client, engine, h, sid, q, llm):  # type: ignore[no-untyped-def]
    r = await client.post(f"/api/v1/sessions/{sid}/qa", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                          json={"question": q})
    assert r.status_code == 202, r.text
    reg = Registry()
    reg.register("qa.answer", make_qa_handler(FakeEmbeddingProvider(), llm))
    w = Worker(engine, reg, lease_seconds=60)
    while await w.run_once():
        pass
    qa = {x["qa_id"]: x for x in (await client.get(f"/api/v1/sessions/{sid}/qa", headers=h)).json()}
    return qa[r.json()["qa_id"]]


FEE = {"text": "The processing fee is 2% of the loan amount plus GST.",
       "citations": [{"anchor": "Processing fee", "quote": "Processing fee | 2% of loan amount + GST"}]}


async def test_ask_routes_and_validator(client, engine, fake_store, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.modules.rag.retrieve.t_ret", lambda path=None: None)  # fake embeddings aren't calibrated
    h, sid = await ready_session(client)
    await upload(client, h, sid, [("kfs.pdf", pdf_bytes(2))])
    await drain(engine, fake_store, KfsParser())

    out = await ask(client, engine, h, sid, "What is the processing fee?", ScriptedLLM([{"sentences": [FEE]}]))
    assert out["status"] == "done" and out["route"] == "quote" and out["answer"]["answer_text"] == FEE["text"]
    assert out["answer"]["citations"][0]["quote"] == "Processing fee | 2% of loan amount + GST"

    computed = {"text": "On Rs 10,00,000 that is Rs 20,000.", "citations": [{"anchor": "Processing fee", "quote": "2% of loan amount"}]}
    llm = ScriptedLLM([{"sentences": [computed]}, {"sentences": [FEE]}])  # invalid, then corrected
    out = await ask(client, engine, h, sid, "Processing fee kitni hai?", llm)
    assert llm.calls == 2 and out["answer"]["answer_text"] == FEE["text"]

    llm = ScriptedLLM([{"sentences": [computed]}, {"sentences": [computed]}])  # invalid twice → fallback
    out = await ask(client, engine, h, sid, "What is the processing fee?", llm)
    assert out["answer"]["answer_text"] == FALLBACK and 1 <= len(out["answer"]["fallback_clauses"]) <= 3
    assert "20,000" not in str(out["answer"])  # 0 LLM-computed numbers delivered

    monkeypatch.setattr("app.modules.verify.service.visibility", lambda: (False, False))  # finance hidden (pre-HC-5)
    assert (await ask(client, engine, h, sid, "How much will I get in hand after fees?", ScriptedLLM([])))["answer"][
        "answer_text"] == NOT_AVAILABLE
    assert (await ask(client, engine, h, sid, "Should I sign this loan?", ScriptedLLM([])))["answer"]["answer_text"] \
        == OUT_OF_SCOPE_TEXT
    out = await ask(client, engine, h, sid, "Is there a gold loan option?", ScriptedLLM([{"not_in_document": True}]))
    assert out["answer"]["not_in_document"] and out["answer"]["answer_text"] == NOT_IN_DOCUMENT
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'qa.answer' "
                                "AND payload->>'status' = 'done'", s=uuid.UUID(sid)) == 6

    # No document in another session → NOT_IN_DOCUMENT without calling the LLM; other users see nothing.
    h2, sid2 = await ready_session(client)
    llm = ScriptedLLM([])
    assert (await ask(client, engine, h2, sid2, "What is the processing fee?", llm))["answer"]["not_in_document"]
    assert llm.calls == 0
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/sessions/{sid}/qa", headers=other)).status_code == 404
    sug = (await client.get(f"/api/v1/sessions/{sid}/qa/suggestions", headers=h)).json()
    assert "What is the processing fee?" in sug and not any(x.startswith("Ask the agent") for x in sug)


async def test_finance_route_after_hc5(client, engine, fake_store):  # type: ignore[no-untyped-def]
    from pathlib import Path

    from app.modules.rag import ask as A
    from tests.integration.test_finance_merge import Ag07Parser

    h, sid = await ready_session(client)
    ag07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()
    await upload(client, h, sid, [("ag07.pdf", ag07)])
    await drain(engine, fake_store, Ag07Parser(with_emi=False))
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))

    async def q(text_: str, visible: bool = True):  # type: ignore[no-untyped-def]
        async with engine.begin() as conn:
            return await A.answer(text_, conn=conn, embedder=FakeEmbeddingProvider(), llm=None, session_id=uuid.UUID(sid),
                                  user_id=owner, finance_visible=visible)

    net = await q("How much will I get in hand after fees?")
    assert net.route == "finance" and "₹9,57,900 in hand" in net.answer_text  # 10,00,000 − 20,000 − 3,600 − 18,500
    assert "₹20,000" in net.answer_text and "₹3,600" in net.answer_text and "18%" in net.answer_text
    assert net.calculation_id and {c["quote"] for c in net.citations} >= {"Rs. 10,00,000", "2% of loan amount + GST"}
    emi = await q("What will my EMI be?")
    assert "₹35,902" in emi.answer_text and "17.5% for 36 months" in emi.answer_text  # KFS prints Rs. 35,902
    total = await q("What is the total amount I will repay?")
    assert "× 36 months" in total.answer_text  # no printed total here: computed
    printed = await q("What is the total amount I will repay?")
    assert printed.calculation_id is not None  # still computed (this KFS prints no total)
    assert (await q("What is the EMI for 3 lakh?")).answer_text == A.OWN_FIGURES_ONLY
    assert (await q("What is the APR?")).answer_text == A.GATED_TEXT
    assert (await q("How much will I get in hand?", visible=False)).answer_text == A.NOT_AVAILABLE
    assert await scalar(engine, "SELECT count(*) FROM calculations WHERE session_id = :s AND enabled",
                        s=uuid.UUID(sid)) == 4  # net, emi, total ×2: every computed answer is traced
    # Through the API/job with finance visible: the qa row links the calculation.
    import app.modules.verify.service as vs

    vs_vis = vs.visibility
    vs.visibility = lambda: (True, False)  # type: ignore[assignment]
    try:
        out = await ask(client, engine, h, sid, "Haath mein kitna milega?", ScriptedLLM([]))
    finally:
        vs.visibility = vs_vis  # type: ignore[assignment]
    assert out["route"] == "finance" and "in hand" in out["answer"]["answer_text"]
    assert await scalar(engine, "SELECT calculation_id IS NOT NULL FROM qa_messages WHERE id = :q",
                        q=uuid.UUID(out["qa_id"])) is True


async def test_finance_route_quotes_a_printed_figure(client, engine, fake_store):  # type: ignore[no-untyped-def]
    from pathlib import Path

    from app.modules.rag import ask as A
    from tests.integration.test_finance_merge import Ag07Parser

    h, sid = await ready_session(client)
    ag07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()
    await upload(client, h, sid, [("ag07.pdf", ag07)])
    await drain(engine, fake_store, Ag07Parser(with_emi=True))  # this KFS prints the EMI
    owner = await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))
    async with engine.begin() as conn:
        a = await A.answer("What will my EMI be per month? emi", conn=conn, embedder=FakeEmbeddingProvider(), llm=None,
                           session_id=uuid.UUID(sid), user_id=owner, finance_visible=True)
        b = await A.finance_answer("What is my EMI?", conn=conn, session_id=uuid.UUID(sid), user_id=owner)
    assert b.answer_text == "Your documents state the EMI: Rs. 35,902 per month." and b.calculation_id is None
    assert b.citations[0]["quote"] == "Rs. 35,902 per month"
    assert a.answer_text  # quote or finance route, never an empty answer
