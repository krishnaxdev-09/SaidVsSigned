"""T3.4 Ask-lite: deterministic routing and the citation validator (doc 09 §5, §7)."""

from __future__ import annotations

import uuid

import pytest

from app.modules.rag.ask import LLMAnswer, route_of, validate
from app.modules.rag.retrieve import RetrievedChunk


def chunk(t: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=uuid.uuid4(), clause_id=uuid.uuid4(), document_id=uuid.uuid4(), document_version=1,
                          page=1, text=t, bm25_rank=1, vec_rank=1, rrf=0.03, similarity=0.9)


REFS = {"C1": chunk("Item | Details\nProcessing fee | 2% of loan amount + GST"),
        "C2": chunk("The Borrower may prepay the loan in full at any time without any charge.")}


@pytest.mark.parametrize(("q", "route"), [
    ("What is the processing fee?", "quote"), ("Is there a foreclosure charge?", "quote"),
    ("Processing fee kitni hai?", "quote"), ("What happens if I pay late?", "quote"),
    ("How much will I get in hand after fees?", "finance"), ("Total kitna padega?", "finance"),
    ("What is the EMI for 3 lakh?", "finance"), ("How much interest will I pay in total?", "finance"),
    ("Should I sign this loan?", "out_of_scope"), ("Which lender is better?", "out_of_scope"),
    ("Is the agent lying?", "out_of_scope"), ("What's the weather?", None),
    ("What is the lender's customer care number?", "quote"), ("Branch ka address kya hai?", "quote"),
])
def test_routing(q: str, route: str | None) -> None:
    assert route_of(q) == route


def a(**kw) -> LLMAnswer:  # type: ignore[no-untyped-def]
    return LLMAnswer.model_validate(kw)


def test_valid_answer() -> None:
    ok = a(sentences=[{"text": "The processing fee is 2% of the loan amount plus GST.",
                       "citations": [{"chunk": "C1", "quote": "Processing fee | 2% of loan amount + GST"}]}])
    assert validate(ok, REFS) == []
    assert validate(a(not_in_document=True), REFS) == []


@pytest.mark.parametrize(("ans", "problem"), [
    ({"sentences": [{"text": "The fee is 2%.", "citations": [{"chunk": "C9", "quote": "2%"}]}]}, "not provided"),
    ({"sentences": [{"text": "The fee is 2%.", "citations": [{"chunk": "C1", "quote": "fee of 2 percent"}]}]}, "not verbatim"),
    ({"sentences": [{"text": "The fee is 2%.", "citations": []}]}, "no citation"),
    ({"sentences": [{"text": "On ₹5,00,000 the fee is ₹10,000.", "citations": [{"chunk": "C1", "quote": "2% of loan amount"}]}]},
     "numbers"),  # the LLM computed a number: always rejected
    ({"sentences": [{"text": "The fee is 2.5%.", "citations": [{"chunk": "C1", "quote": "2% of loan amount"}]}]}, "numbers"),
    ({"sentences": []}, "no sentences"),
    ({"not_in_document": True, "sentences": [{"text": "x", "citations": []}]}, "not_in_document"),
])
def test_validator_rejects(ans: dict, problem: str) -> None:
    errs = validate(a(**ans), REFS)
    assert errs and any(problem in e for e in errs), errs
