"""doc_terms — doc 05 §6 step 7 (T2.3).

1. **Lexicon pass** (deterministic) over KFS-style rows: a table row (or a "Label: value" line) whose label matches a
   KFS field → term with `value_raw` = the value cell(s).
2. **LLM structured pass** (Gemma, ADR-004) over the remaining clauses that carry a number or a zero-charge phrase:
   clause → `{field, value_raw, quantity, basis, rate_type, conditions, includes_gst}`. `value_raw` must be a verbatim
   substring of the clause; PII is redacted before the call. A schema failure after the provider's one corrective
   retry skips the LLM terms (lexicon terms and retrieval still work).
In both passes **`value` comes only from the doc 04 normaliser** (`normaliser_version` recorded); unparsed → None.
Categorical labels (basis, rate_type, includes_gst) prefer the normaliser's reading of the text, then the LLM's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from app.core.retry import SchemaError
from app.llm.base import LLMProvider
from app.llm.redact import contains_placeholder, redact
from app.modules.claims.normalize import NORMALISER_VERSION, normalise
from app.modules.documents.segment import NUMBERING, Clause
from app.schemas.claims import ExtractedValue

log = structlog.get_logger()

# Order matters: more specific labels first ("Net disbursed amount" before "loan amount").
LEXICON: list[tuple[str, re.Pattern[str]]] = [(f, re.compile(p, re.IGNORECASE)) for f, p in (
    ("net_disbursement", r"net\s+(?:amount\s+)?disburs|disburs(?:ed|al)\s+amount|amount\s+disbursed"),
    ("total_cost", r"total\s+(?:amount\s+)?payable|total\s+cost|total\s+repayment"),
    ("processing_fee", r"processing\s+(?:fee|charge)"),
    ("prepayment_charge", r"pre-?\s?payment|fore-?\s?clos|part[\s-]?payment"),
    ("penal_charge", r"penal|late[\s-]payment|delayed\s+payment"),
    ("insurance_bundled", r"insurance|credit[\s-]?shield|life\s+cover"),
    ("other_charge", r"bounce|dishono(?:u)?r|documentation|stamp(?:ing)?\s+(?:duty|charge)|legal\s+(?:fee|charge)"),
    ("interest_rate", r"rate\s+of\s+interest|interest\s+rate|\broi\b"),
    ("emi", r"\bemi\b|equated\s+monthly|instal?ment\s+amount"),
    ("tenure", r"tenure|loan\s+term|term\s+of\s+(?:the\s+)?loan|repayment\s+period"),
    ("loan_amount", r"(?:loan|sanction(?:ed)?|principal)\s+amount|amount\s+of\s+(?:the\s+)?loan|^loan$"),
)]
AMOUNT_FIELDS = {"loan_amount", "emi", "net_disbursement", "total_cost", "insurance_bundled", "other_charge"}
FEE_FIELDS = {"processing_fee", "prepayment_charge", "penal_charge"}
TERM_FIELDS = AMOUNT_FIELDS | FEE_FIELDS | {"interest_rate", "tenure"}
UNIT = {"amount_inr": "INR", "percent": "%", "months": "months", "years": "months", "count": "count", "boolean": "bool"}
_INCL_GST = re.compile(r"\b(?:incl(?:\.|usive|uding)?)\s+(?:of\s+)?gst\b", re.IGNORECASE)
_LABEL_LINE = re.compile(r"^([A-Za-z][A-Za-z ()/&.-]{2,60}?)\s*[:–-]\s+(.+)$")
_LLM_CANDIDATE = re.compile(r"[0-9०-९%₹]|\b(?:nil|free|no|without|zero|waived)\b", re.IGNORECASE)


@dataclass
class Term:
    clause_ord: int
    field: str
    value_raw: str
    value: Decimal | None
    unit: str
    basis: str | None
    rate_type: str | None
    includes_gst: bool | None
    conditions: list[str] = dc_field(default_factory=list)
    source: Literal["lexicon", "llm"] = "lexicon"
    normaliser_version: str | None = None


def lexicon_field(label: str) -> str | None:
    for f, pat in LEXICON:
        if pat.search(label):
            return f
    return None


def quantity_of(fld: str, value_raw: str) -> str:
    if fld == "interest_rate":
        return "percent"
    if fld == "tenure":
        return "years" if re.search(r"\byears?\b|\byrs?\b", value_raw, re.IGNORECASE) else "months"
    if fld in FEE_FIELDS:
        return "percent" if "%" in value_raw else "amount_inr"
    return "amount_inr"


def _label(v: str | None) -> str | None:
    return None if v in (None, "unspecified") else v


def build_term(clause: Clause, fld: str, quantity: str, value_raw: str, *, source: Literal["lexicon", "llm"],
               basis: str | None = None, rate_type: str | None = None, includes_gst: bool | None = None,
               conditions: list[str] | None = None) -> Term:
    ev = normalise(ExtractedValue(field=fld, raw_text=value_raw, quantity=quantity),  # type: ignore[arg-type]
                   context=clause.text)
    gst = ev.includes_gst
    if gst is None and _INCL_GST.search(value_raw):
        gst = True
    if gst is None:
        gst = includes_gst
    return Term(clause_ord=clause.ord, field=fld, value_raw=value_raw, value=ev.value, unit=UNIT[ev.quantity],
                basis=_label(ev.rate_basis) or basis, rate_type=_label(ev.rate_type) or rate_type, includes_gst=gst,
                conditions=conditions or [], source=source, normaliser_version=ev.normaliser_version)


def lexicon_terms(clauses: list[Clause]) -> list[Term]:
    out = []
    for c in clauses:
        if c.is_table_row and len(c.cells) >= 2:
            idx = next((k for k, cell in enumerate(c.cells[:-1]) if lexicon_field(cell)), None)
            if idx is None:
                continue
            fld, raw = lexicon_field(c.cells[idx]), " ".join(x for x in c.cells[idx + 1:] if x).strip()
        elif not c.is_table_row and "\n" not in c.text and (m := _LABEL_LINE.match(c.text)):
            fld, raw = lexicon_field(m.group(1)), m.group(2).strip()
        else:
            continue
        if fld and raw:
            out.append(build_term(c, fld, quantity_of(fld, raw), raw, source="lexicon"))
    return out


# ---- LLM structured pass ------------------------------------------------------------------------------------------

class LLMTerm(BaseModel):
    clause: str = Field(description="clause ref, e.g. C3")
    field: Literal["loan_amount", "interest_rate", "tenure", "emi", "processing_fee", "net_disbursement",
                   "prepayment_charge", "penal_charge", "insurance_bundled", "other_charge", "total_cost"]
    value_raw: str = Field(min_length=1, description="verbatim span of the clause that states the value")
    quantity: Literal["amount_inr", "percent", "months", "years"]
    rate_basis: Literal["reducing", "flat"] | None = None
    rate_type: Literal["fixed", "floating"] | None = None
    includes_gst: bool | None = None
    conditions: list[str] = Field(default_factory=list)


class LLMTermsOutput(BaseModel):
    terms: list[LLMTerm]


SYSTEM_PROMPT = """You map loan-agreement clauses to structured loan terms. Each input line is `C<n>: <clause text>`.
For every clause that states a loan term, output one item per term with:
- clause: the clause ref (e.g. "C3").
- field: one of loan_amount, interest_rate, tenure, emi, processing_fee, net_disbursement, prepayment_charge,
  penal_charge, insurance_bundled, other_charge (bounce, documentation, stamping, legal and similar charges), total_cost.
- value_raw: the exact words from the clause that state the value, copied character for character (e.g. "2% of the loan
  amount", "Rs. 12,000", "without any charge"). Never compute, convert or round a number.
- quantity: amount_inr, percent, months or years.
- rate_basis (reducing|flat) and rate_type (fixed|floating) only when the clause says so; otherwise null.
- includes_gst: true if the value includes GST, false if GST is charged on top ("plus GST", "+ GST"), else null.
- conditions: short verbatim phrases that limit when the term applies (e.g. "after 6 EMIs"); empty if none.
Skip clauses that state no loan term (governing law, definitions, signatures). Placeholders like [PAN] are redacted
personal data; never use them as values. Answer only with JSON: {"terms": [...]}."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def llm_candidates(clauses: list[Clause], covered: set[int]) -> list[Clause]:
    return [c for c in clauses if c.ord not in covered and c.label not in ("signature", "section")
            and _LLM_CANDIDATE.search(NUMBERING.sub("", c.text, count=1))]  # the clause number itself is not a value


async def llm_terms(llm: LLMProvider, clauses: list[Clause], covered: set[int]) -> tuple[list[Term], dict[str, int]]:
    """Returns (terms, stats). Stats count dropped LLM items by reason (for logs / evaluation)."""
    cands = llm_candidates(clauses, covered)
    stats = {"candidates": len(cands), "proposed": 0, "kept": 0, "dropped_ref": 0, "dropped_not_verbatim": 0,
             "dropped_placeholder": 0, "dropped_duplicate": 0, "schema_failed": 0}
    if not cands:
        return [], stats
    refs = {f"C{k + 1}": c for k, c in enumerate(cands)}
    body = "\n".join(f"{r}: {redact(c.text.replace(chr(10), ' '))}" for r, c in refs.items())
    try:
        out = await llm.structured(system=SYSTEM_PROMPT, messages=[{"role": "user", "content": body}],
                                   schema=LLMTermsOutput, temperature=0.0, max_tokens=3000, timeout_s=120)
    except SchemaError:
        stats["schema_failed"] = 1
        log.warning("doc_terms.llm_schema_failed", candidates=len(cands))
        return [], stats
    assert isinstance(out, LLMTermsOutput)
    terms: list[Term] = []
    seen: set[tuple[int, str, str]] = set()
    for t in out.terms:
        stats["proposed"] += 1
        c = refs.get(t.clause.strip())
        if c is None:
            stats["dropped_ref"] += 1
            continue
        if contains_placeholder(t.value_raw):
            stats["dropped_placeholder"] += 1
            continue
        if _norm(t.value_raw) not in _norm(c.text):
            stats["dropped_not_verbatim"] += 1
            continue
        key = (c.ord, t.field, _norm(t.value_raw))
        if key in seen:
            stats["dropped_duplicate"] += 1
            continue
        seen.add(key)
        quantity = "percent" if t.field in FEE_FIELDS | {"interest_rate"} and "%" in t.value_raw else t.quantity
        terms.append(build_term(c, t.field, quantity, t.value_raw, source="llm", basis=t.rate_basis,
                                rate_type=t.rate_type, includes_gst=t.includes_gst, conditions=t.conditions))
        stats["kept"] += 1
    return terms, stats


__all__ = ["NORMALISER_VERSION", "TERM_FIELDS", "Term", "build_term", "lexicon_terms", "llm_terms"]
