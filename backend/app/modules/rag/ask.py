"""Ask Your Contract (lite) — doc 09 §5 (T3.4).

Routing: a deterministic intent check first (calculation intent → finance; legal advice / lender recommendation →
out_of_scope); a question that names no loan topic goes to a structured LLM classifier. Routes:
- quote: hybrid retrieval (doc 09 §4) → Gemma answer as cited sentences → **citation validator** (cited chunks were
  provided; quotes verbatim; every sentence cited; every number appears in a cited quote — the LLM may not compute).
  1 corrective retry, then "I couldn't find a reliable answer in your document" + the top 3 clauses. Nothing
  retrieved above T_ret → NOT_IN_DOCUMENT.
- finance: before HC-5 every answer is "This calculation isn't available yet." After HC-5 (finance visible) the
  answer is computed deterministically from the documents' own terms (SourcedFactory → MVP formulas, doc 06), stored
  as a calculation trace, templated, and cites the source clauses. Amounts in the question are never used as inputs;
  APR / effective rate stay unavailable (gated for the whole MVP).
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
OWN_FIGURES_ONLY = ("I can only calculate with the figures in your documents. For a different amount, ask the lender "
                    "for a written quote.")
GATED_TEXT = "The APR and effective rate aren't calculated in this version. Ask the lender for the APR in writing."
NO_DOC_FIGURES = "Your documents don't give enough figures for this. Missing: {missing}. Ask the lender to put it in writing."
CALC_KIND = [  # (kind, pattern): first match wins
    ("gated", re.compile(r"\bapr\b|effective (rate|interest)|annual percentage", re.I)),
    ("net", re.compile(r"in hand|haath|receive|get after|after (all )?fees|disburs|deduct", re.I)),
    ("interest", re.compile(r"interest (amount|kitna)|how much interest|total interest|byaj kitna", re.I)),
    ("total", re.compile(r"total|repay|pay (back|in all|over)|padega|dena|full tenure", re.I)),
    ("emi", re.compile(r"\bemi\b|kist|instal", re.I)),
]
MISSING_LABEL = {"principal": "loan amount", "annual_rate_pct": "interest rate", "tenure_months": "tenure"}


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
- Loan terms have synonyms: foreclosure = prepayment = part-payment = closing the loan early; processing fee = login
  fee = file charge; EMI = kist = instalment; penal charge = late-payment charge. A chunk about a synonym answers the
  question.
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
    calculation_id: str | None = None


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
        if not finance_visible:
            return Answer(route, NOT_AVAILABLE)  # HC-5 not approved: no finance answers (doc 06 §4a)
        return await finance_answer(question, conn=conn, session_id=session_id, user_id=user_id)
    quoted = await _quote_answer(question, conn=conn, embedder=embedder, llm=llm, session_id=session_id, user_id=user_id)
    # A figure the documents don't print (e.g. no EMI row) can still be computed from their terms after HC-5.
    calc = next((k for k, pat in CALC_KIND if pat.search(question)), None)
    if quoted.not_in_document and finance_visible and calc not in (None, "gated") and not numbers(question):
        fin = await finance_answer(question, conn=conn, session_id=session_id, user_id=user_id)
        if fin.calculation_id:
            return fin
    return quoted


async def _quote_answer(question: str, *, conn, embedder: EmbeddingProvider, llm: LLMProvider | None,  # type: ignore[no-untyped-def]
                        session_id: uuid.UUID, user_id: uuid.UUID) -> Answer:
    route: Route = "quote"
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


def inr(x: Decimal) -> str:
    """₹ with Indian digit grouping, paise only when present (12,34,567 / 1,234.50)."""
    q = x.quantize(Decimal("0.01"))
    whole, frac = f"{q:.2f}".split(".")
    neg, whole = whole.startswith("-"), whole.lstrip("-")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    out = ",".join([head, *groups, tail] if head else [*groups, tail])
    return ("-" if neg else "") + "₹" + out + ("" if frac == "00" else "." + frac)


async def finance_answer(question: str, *, conn, session_id: uuid.UUID, user_id: uuid.UUID) -> Answer:  # type: ignore[no-untyped-def]
    """Deterministic calculation from the documents' terms (doc 09 §5 finance route, HC-5 approved MVP formulas)."""
    from sqlalchemy import text

    from app.modules.finance import formulas as F
    from app.modules.finance import trace
    from app.modules.finance.engine import load_document_inputs
    from app.modules.finance.sourced import SourcedFactory

    kind = next((k for k, pat in CALC_KIND if pat.search(question)), None)
    if kind == "gated":
        return Answer("finance", GATED_TEXT)
    if kind is None:
        return Answer("finance", NOT_AVAILABLE)
    if numbers(question):  # "EMI for 3 lakh": inputs must come from the documents, never from the question
        return Answer("finance", OWN_FIGURES_ONLY)
    printed_field = {"net": "net_disbursement", "total": "total_cost", "emi": "emi"}.get(kind)
    if printed_field:  # the documents state this figure: quote it rather than recompute it (no rounding drift)
        row = (await conn.execute(text(
            "SELECT t.value_raw, ch.id AS chunk_id FROM doc_terms t JOIN clauses c ON c.id = t.clause_id "
            "JOIN documents d ON d.id = c.document_id AND d.version = c.version JOIN sessions s ON s.id = d.session_id "
            "JOIN chunks ch ON ch.clause_id = c.id WHERE d.session_id = :s AND s.owner_id = :u AND d.status = 'ready' "
            "AND d.deleted_at IS NULL AND t.field = :f AND t.value IS NOT NULL ORDER BY c.ord LIMIT 1"),
            {"s": session_id, "u": user_id, "f": printed_field})).first()
        if row is not None:
            label = {"net": "the amount you receive", "total": "the total amount payable", "emi": "the EMI"}[kind]
            return Answer("finance", f"Your documents state {label}: {row.value_raw}.",
                          [{"chunk_id": str(row.chunk_id), "quote": row.value_raw}])
    if kind == "interest":  # printed total payable − loan amount, when the documents print both (no rounding drift)
        rows = {r.field: r for r in (await conn.execute(text(
            "SELECT DISTINCT ON (t.field) t.id, t.field, t.value, t.value_raw, t.normaliser_version, c.text_source, "
            "c.ocr_conf, c.ocr_min_numeric_conf, ch.id AS chunk_id FROM doc_terms t JOIN clauses c ON c.id = t.clause_id "
            "JOIN documents d ON d.id = c.document_id AND d.version = c.version JOIN sessions s ON s.id = d.session_id "
            "JOIN chunks ch ON ch.clause_id = c.id WHERE d.session_id = :s AND s.owner_id = :u AND d.status = 'ready' "
            "AND d.deleted_at IS NULL AND t.field IN ('total_cost', 'loan_amount') AND t.value IS NOT NULL "
            "ORDER BY t.field, c.ord"), {"s": session_id, "u": user_id})).mappings().all()}
        if {"total_cost", "loan_amount"} <= set(rows):
            fac = SourcedFactory()
            tot, loan = (fac.from_doc_term(rows[f], rows[f]) for f in ("total_cost", "loan_amount"))
            res = F.Result("total_interest_v1", {"total_repayment": tot.value, "principal": loan.value,
                                                 "total_interest": F.q(tot.value - loan.value)})
            cid = await trace.record(conn, session_id, res, {"total_repayment": tot, "principal": loan},
                                     tolerance_row="", enabled=True)
            return Answer("finance", f"From your documents, the total interest is {inr(res.outputs['total_interest'])} "
                          f"(total amount payable {inr(tot.value)} − loan {inr(loan.value)}).",
                          [{"chunk_id": str(rows[f]["chunk_id"]), "quote": rows[f]["value_raw"]}
                           for f in ("total_cost", "loan_amount")], calculation_id=str(cid))
    loaded = await load_document_inputs(conn, session_id, user_id, SourcedFactory())
    if loaded is None:
        return Answer("finance", NOT_IN_DOCUMENT, not_in_document=True)
    inp, clause_of = loaded
    res = (F.net_disbursement(inp) if kind == "net" else F.total_repayment(inp) if kind == "total"
           else F.total_interest(inp) if kind == "interest" else F.emi(inp))
    if not res.ok:
        missing = ", ".join(MISSING_LABEL.get(m, m) for m in res.missing)
        return Answer("finance", NO_DOC_FIGURES.format(missing=missing))
    used = {k: v for k, v in (("principal", inp.principal), ("annual_rate_pct", inp.annual_rate_pct),
                              ("tenure_months", inp.tenure_months), ("processing_fee_pct", inp.processing_fee_pct),
                              ("processing_fee_abs", inp.processing_fee_abs), ("gst_rate_pct", inp.gst_rate_pct))
            if v is not None} | {f"deduction_{i}": d for i, d in enumerate(inp.other_upfront_deductions)}
    if kind != "net":
        used = {k: v for k, v in used.items() if k in ("principal", "annual_rate_pct", "tenure_months")}
    cid = await trace.record(conn, session_id, res, used, tolerance_row="", enabled=True)
    o = res.outputs
    p = inp.principal.value if inp.principal else Decimal(0)
    n = int(inp.tenure_months.value) if inp.tenure_months else 0
    rate = inp.annual_rate_pct.value if inp.annual_rate_pct else Decimal(0)
    if kind == "net":
        parts = [f"loan {inr(p)}", f"processing fee {inr(o['fee'])}", f"GST {inr(o['gst'])}"]
        if o["other_deductions"]:
            parts.append(f"other deductions {inr(o['other_deductions'])}")
        text_ = f"From your documents, you would receive {inr(o['net_disbursement'])} in hand ({' − '.join(parts)})."
    elif kind == "emi":
        text_ = f"From your documents, the EMI works out to {inr(o['emi'])} per month ({inr(p)} at {rate}% for {n} months)."
    elif kind == "total":
        text_ = (f"From your documents, you would repay {inr(o['total_repayment'])} in total "
                 f"(EMI {inr(o['emi'])} × {n} months).")
    else:
        text_ = (f"From your documents, the total interest is {inr(o['total_interest'])} "
                 f"(total repayment {inr(o['total_repayment'])} − loan {inr(p)}).")
    notes = []
    if any("GST" in a for a in res.assumptions) and kind == "net":
        notes.append("GST is taken as 18% unless your documents say otherwise.")
    if inp.rate_basis == "unspecified" and kind != "net":
        notes.append("Your documents don't say whether the rate is reducing or flat; this assumes reducing.")
    notes.append("Calculated by SaidSigned from your documents; confirm the final figures with the lender.")
    cites = []
    for s_ in used.values():
        if s_.source != "document" or s_.ref_id not in clause_of:
            continue
        clause_id = clause_of[s_.ref_id]["id"]
        chunk = (await conn.execute(text("SELECT id FROM chunks WHERE clause_id = :c ORDER BY id LIMIT 1"),
                                    {"c": clause_id})).scalar()
        if chunk is not None and s_.note:
            cites.append({"chunk_id": str(chunk), "quote": s_.note})
    return Answer("finance", " ".join([text_, *notes]), cites, calculation_id=str(cid))
