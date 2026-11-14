"""Ask Your Contract (lite) — doc 09 §5 (T3.4).

Routing: a deterministic intent check first (calculation intent → finance; legal advice / lender recommendation →
out_of_scope); a question that names no loan topic goes to a structured LLM classifier. Routes:
- quote: hybrid retrieval (doc 09 §4) → Gemma answer as cited sentences → **citation validator** (cited chunks were
  provided; quotes verbatim; every sentence cited; every number appears in a cited quote — the LLM may not compute).
  1 corrective retry, then "I couldn't find a reliable answer in your document" + the top 3 clauses. Nothing
  retrieved above T_ret → NOT_IN_DOCUMENT.
- finance: before HC-5 every answer is "This calculation isn't available yet."
- out_of_scope: a fixed polite refusal + "Ask the lender".
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from app.core.retry import SchemaError
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.redact import redact
from app.modules.claims.normalize import _phrases, eval_phrase, tokens
from app.modules.rag.retrieve import RetrievedChunk, retrieve

log = structlog.get_logger()
Route = Literal["quote", "finance", "out_of_scope"]

FINANCE_INTENT = re.compile(
    r"\b(total|kitna padega|kitna dena|how much (will|would|do) i (pay|get|receive)|emi for|after (all )?fees|in hand|"
    r"haath mein|calculate|calculation|compute|kitni emi banegi|apr|effective (rate|interest)|interest (amount|kitna)|"
    r"how much interest|total interest|what will i (pay|receive))\b", re.IGNORECASE)
OUT_OF_SCOPE = re.compile(
    r"\b(should i (sign|take|accept|go)|is (it|this) (legal|illegal|a good deal|worth)|sue|lawyer|court case|complain to|"
    r"which (lender|bank) is better|better lender|recommend|advice|cibil score|credit score|is the agent (lying|honest)|"
    r"can i trust|loan lena chahiye|lena chahiye)\b", re.IGNORECASE)
LOAN_TOPIC = re.compile(
    r"fee|charge|interest|rate|emi|kist|tenure|month|amount|loan|prepay|foreclos|penal|late|insurance|gst|disburs|"
    r"repay|nach|court|jurisdiction|agreement|kfs|clause|document|bounce|stamp|byaj|mahine|lagega|lagegi|deduct|"
    r"lender|branch|customer care|contact|helpline|address|grievance|complaint|nodal|kyc",
    re.IGNORECASE)

NOT_AVAILABLE = "This calculation isn't available yet."
OUT_OF_SCOPE_TEXT = ("I can only answer from your loan documents. I can't give legal or financial advice or "
                     "recommend lenders. Ask the lender, or a qualified adviser.")
NOT_IN_DOCUMENT = "Your documents don't appear to say this. Ask the lender to put it in writing."
FALLBACK = "I couldn't find a reliable answer in your document. These clauses may help:"


class Citation(BaseModel):
    chunk: str = Field(description="context ref, e.g. C2")
    quote: str = Field(min_length=1, description="exact words copied from that chunk")


class Sentence(BaseModel):
    text: str = Field(min_length=1)
    citations: list[Citation]


class LLMAnswer(BaseModel):
    not_in_document: bool = False
    sentences: list[Sentence] = []


class RouteOut(BaseModel):
    route: Route


ANSWER_PROMPT = """You answer a customer's question using ONLY the numbered context chunks from their loan documents.
Rules:
- Answer in short sentences. Every sentence must cite at least one chunk with the exact words copied from it.
- Never calculate, convert or add numbers. Only repeat numbers exactly as they appear in the cited quote.
- If the chunks don't answer the question, set not_in_document to true and give no sentences.
- No legal or financial advice. Answer in English.
Reply only with JSON: {"not_in_document": false, "sentences": [{"text": "...", "citations": [{"chunk": "C1",
"quote": "exact words"}]}]}"""

ROUTE_PROMPT = """Classify a customer's question about their loan documents. Reply only with JSON {"route": "..."}:
"quote" — what the documents say (fees, rates, terms, clauses); "finance" — needs a calculation (totals, amounts in
hand, EMI for a different amount); "out_of_scope" — legal/financial advice, lender recommendations, anything else."""


@dataclass
class Answer:
    route: Route
    answer_text: str
    citations: list[dict[str, str]] = field(default_factory=list)  # {chunk_id, quote}
    not_in_document: bool = False
    fallback_clauses: list[dict[str, str]] = field(default_factory=list)  # {chunk_id, page, text}
    validator_errors: list[str] = field(default_factory=list)
    attempts: int = 0


def route_of(question: str) -> Route | None:
    if OUT_OF_SCOPE.search(question):
        return "out_of_scope"
    if FINANCE_INTENT.search(question):
        return "finance"
    if LOAN_TOPIC.search(question):
        return "quote"
    return None  # ambiguous → LLM classifier


def numbers(text: str) -> set[Decimal]:
    words = " ".join(tokens(text)).split()
    out = set()
    for a, b in _phrases(words, False):
        r = eval_phrase(words[a:b], False)
        if r is not None:
            out.add(r[0])
    return out


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def validate(ans: LLMAnswer, refs: dict[str, RetrievedChunk]) -> list[str]:
    """doc 09 §5 citation validator. Returns the problems (empty = valid)."""
    errs: list[str] = []
    if ans.not_in_document:
        return [] if not ans.sentences else ["not_in_document with sentences"]
    if not ans.sentences:
        return ["no sentences"]
    for i, s in enumerate(ans.sentences, start=1):
        if not s.citations:
            errs.append(f"sentence {i} has no citation")
        quoted: set[Decimal] = set()
        for c in s.citations:
            chunk = refs.get(c.chunk.strip())
            if chunk is None:
                errs.append(f"sentence {i} cites {c.chunk}, which was not provided")
                continue
            if _n(c.quote) not in _n(chunk.text):
                errs.append(f"sentence {i}: quote is not verbatim from {c.chunk}")
                continue
            quoted |= numbers(c.quote)
        extra = numbers(s.text) - quoted
        if extra:
            errs.append(f"sentence {i}: numbers {sorted(str(x) for x in extra)} are not in its cited quotes")
    return errs


async def answer(question: str, *, conn, embedder: EmbeddingProvider, llm: LLMProvider | None,  # type: ignore[no-untyped-def]
                 session_id: uuid.UUID, user_id: uuid.UUID, finance_visible: bool = False) -> Answer:
    route = route_of(question)
    if route is None:
        route = "out_of_scope"
        if llm is not None:
            try:
                out = await llm.structured(system=ROUTE_PROMPT, messages=[{"role": "user", "content": redact(question)}],
                                           schema=RouteOut, temperature=0.0, max_tokens=50, timeout_s=20)
                assert isinstance(out, RouteOut)
                route = out.route
            except SchemaError:
                pass
    if route == "out_of_scope":
        return Answer(route, OUT_OF_SCOPE_TEXT)
    if route == "finance":
        return Answer(route, NOT_AVAILABLE)  # HC-5 pending: no finance answers (doc 06 §4a)
    hits = await retrieve(conn, embedder, session_id=session_id, user_id=user_id, query=question)
    if not hits:
        return Answer(route, NOT_IN_DOCUMENT, not_in_document=True)
    refs = {f"C{i + 1}": h for i, h in enumerate(hits)}
    if llm is None:
        return _fallback(route, hits, ["no llm"])
    context = "\n\n".join(f"{k} (page {h.page}): {redact(h.text)}" for k, h in refs.items())
    messages: list[dict] = [{"role": "user", "content": f"Question: {redact(question)}\n\nContext:\n{context}"}]
    errs: list[str] = []
    for attempt in (1, 2):
        try:
            out = await llm.structured(system=ANSWER_PROMPT, messages=messages, schema=LLMAnswer, temperature=0.0,
                                       max_tokens=800, timeout_s=60)
        except SchemaError:
            errs = ["schema"]
            break
        assert isinstance(out, LLMAnswer)
        errs = validate(out, refs)
        if not errs:
            if out.not_in_document:
                return Answer(route, NOT_IN_DOCUMENT, not_in_document=True, attempts=attempt)
            cites = [{"chunk_id": str(refs[c.chunk.strip()].chunk_id), "quote": c.quote}
                     for s in out.sentences for c in s.citations]
            return Answer(route, " ".join(s.text.strip() for s in out.sentences), cites, attempts=attempt)
        log.info("qa.citation_invalid", attempt=attempt, problems=errs)
        messages += [{"role": "assistant", "content": out.model_dump_json()},
                     {"role": "user", "content": "Your answer failed validation: " + "; ".join(errs)
                      + ". Fix it: copy quotes exactly, cite every sentence, and use only numbers from the quotes."}]
    return _fallback(route, hits, errs)


def _fallback(route: Route, hits: list[RetrievedChunk], errs: list[str]) -> Answer:
    return Answer(route, FALLBACK, fallback_clauses=[{"chunk_id": str(h.chunk_id), "page": str(h.page), "text": h.text}
                                                     for h in hits[:3]], validator_errors=errs, attempts=2)
