"""Contract engine — said vs signed (doc 05 §7, T2.6).

1. No ready document → NOT_VERIFIED(no_document).
2. Candidates: doc_terms of the claim's field (current versions of this session's ready documents), else hybrid
   retrieval top 3 (T_ret). Nothing → NOT_VERIFIED(clause_not_found), or page_unreadable when a page failed OCR.
3. Numeric path (claim values parsed by the normaliser, document values from doc_terms — never the LLM): doc 06
   tolerance table. Zero claims ("no charge") compare in any unit. Percent ↔ INR needs the loan amount (a doc 06
   formula) → NOT_VERIFIED(calculation_not_enabled) before HC-5. GST: "2%" vs "2% + GST" → MATCH on the base fee
   (note); "including GST" vs "+ GST" → UNCLEAR(assumption_dependent) (the total depends on the assumed GST rate).
   Basis: flat vs reducing at the same number → CONFLICT on basis.
4. Semantic path (no comparable value): Gemma judge (label + verbatim clause span) and the NLI cross-encoder must
   agree: both contradict → CONFLICT, both entail → MATCH, both neutral → clause_not_found, else
   UNCLEAR(semantic_disagreement).
5. A conditional claim that would be a CONFLICT → UNCLEAR(condition_mismatch).
6. OCR gate: the clause's ocr_conf < T_ocr or (numeric) ocr_min_numeric_conf < T_ocr_num, or unknown → UNCLEAR.
7. DocumentEvidence is built from DB rows only.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.retry import SchemaError
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.nli import NLIProvider
from app.llm.redact import redact
from app.modules.finance.tolerance import compare
from app.modules.rag import retrieve
from app.modules.verify.merge import Outcome
from app.modules.verify.service import EngineContext
from app.schemas.claims import Claim, ExtractedValue
from app.schemas.verification import DocumentEvidence, EngineVerdict, FindingKind, NormBBox, ReasonCode, Status
from app.schemas.wording import QUESTION_TEMPLATES, REASON_TEXT

log = structlog.get_logger()
CONTRACT_TYPES = {"processing_fee", "other_charge", "interest_rate", "tenure", "loan_amount", "prepayment_charge",
                  "penal_charge", "insurance_bundled", "document_assurance", "general_assurance", "emi", "total_cost",
                  "net_disbursement"}
UNIT_OF = {"percent": "%", "amount_inr": "INR", "months": "months"}
SEMANTIC_TOP = 3


class JudgeOut(BaseModel):
    label: Literal["entails", "contradicts", "neutral"]
    clause: str = Field(description="clause ref, e.g. C2; empty when neutral")
    span: str = Field(default="", description="verbatim span of that clause supporting the label")


JUDGE_PROMPT = """You compare a statement a loan agent made with clauses from the customer's loan documents.
Decide whether the clauses ENTAIL the statement (the documents say the same thing), CONTRADICT it (the documents
say something incompatible), or are NEUTRAL (the clauses don't address it). Judge only what is written; never
compute numbers. Quote the exact words of the deciding clause in `span`, copied character for character.
Answer only with JSON: {"label": "entails|contradicts|neutral", "clause": "C1", "span": "..."}"""


def _v(engine_status: Status, code: ReasonCode | None = None, evidence: list | None = None,
       question: str | None = None, missing: list[str] | None = None, assumptions: list[str] | None = None,
       reason: str | None = None) -> EngineVerdict:
    return EngineVerdict(engine="contract", status=engine_status, reason_code=code,
                         finding_kind=FindingKind.CONTRACTUAL if engine_status in (Status.CONFLICT, Status.MATCH) else None,
                         reason=reason or (REASON_TEXT[code] if code else "Compared with the document."),
                         evidence=evidence or [], suggested_question=question, missing=missing or [],
                         assumptions=assumptions or [])


def doc_evidence(row: Any) -> DocumentEvidence:
    page = row["page_start"]
    boxes = [NormBBox(x0=b["bbox"][0], y0=b["bbox"][1], x1=b["bbox"][2], y1=b["bbox"][3])
             for b in row["bboxes"] or [] if b["bbox"][2] > b["bbox"][0] and b["bbox"][3] > b["bbox"][1]]
    return DocumentEvidence(document_id=row["document_id"], document_version=row["version"], page=page,
                            printed_page=None, clause_id=row["id"], clause_text=row["text"], bboxes=boxes,
                            ocr_conf=row["ocr_conf"], ocr_min_numeric_conf=row["ocr_min_numeric_conf"],
                            text_source=row["text_source"] or "text_layer")


def ocr_ok(row: Any, numeric: bool, t_ocr: float, t_ocr_num: float) -> bool:
    if (row["text_source"] or "text_layer") == "text_layer":
        return True
    if row["ocr_conf"] is None or row["ocr_conf"] < t_ocr:
        return False
    return not numeric or (row["ocr_min_numeric_conf"] is not None and row["ocr_min_numeric_conf"] >= t_ocr_num)


_SCOPE = ("FROM clauses c JOIN documents d ON d.id = c.document_id AND d.version = c.version "
          "JOIN sessions s ON s.id = d.session_id WHERE d.session_id = :s AND s.owner_id = :u AND d.status = 'ready' "
          "AND d.deleted_at IS NULL")


class ContractEngine:
    name: Literal["contract"] = "contract"
    shadow = False

    def __init__(self, embedder: EmbeddingProvider | None = None, llm: LLMProvider | None = None,
                 nli: NLIProvider | None = None) -> None:
        self.embedder, self.llm, self.nli = embedder, llm, nli

    def applies(self, claim: Claim) -> bool:
        return claim.claim_type.value in CONTRACT_TYPES

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome:
        conn, p = ctx.conn, {"s": ctx.session_id, "u": ctx.owner_id}
        docs = (await conn.execute(text(
            "SELECT d.id, d.warnings FROM documents d JOIN sessions s ON s.id = d.session_id WHERE d.session_id = :s "
            "AND s.owner_id = :u AND d.status = 'ready' AND d.deleted_at IS NULL"), p)).all()
        if not docs:
            return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.NO_DOCUMENT,
                              question=QUESTION_TEMPLATES[ReasonCode.NO_DOCUMENT]))
        unreadable = sorted({int(w.split(":")[1]) for d in docs for w in d.warnings or [] if w.startswith("page_unreadable:")})
        field = claim.claim_type.value
        scope = _SCOPE.replace("FROM clauses c", "FROM doc_terms t JOIN clauses c ON c.id = t.clause_id")
        terms = (await conn.execute(text(
            f"SELECT t.field, t.value, t.unit, t.basis, t.rate_type, t.includes_gst, t.value_raw, c.* {scope} "
            "AND t.field = :f AND t.version = c.version ORDER BY c.ord"), {**p, "f": field})).mappings().all()
        values = [v for v in claim.values if v.normaliser_version and (v.value is not None or v.value_low is not None)]
        if values and terms and any(t["value"] is not None for t in terms):
            out = self._numeric(claim, values[0], [t for t in terms if t["value"] is not None], ctx)
            if out is not None:
                return out
        rows = list(terms)
        if self.embedder is not None:
            hits = await retrieve.retrieve(conn, self.embedder, session_id=ctx.session_id, user_id=ctx.owner_id,
                                           query=claim.normalized_statement or claim.quote, k=SEMANTIC_TOP,
                                           threshold=ctx.thresholds.T_ret)
            ids = [h.clause_id for h in hits if h.clause_id not in {r["id"] for r in rows}]
            if ids:
                rows += (await conn.execute(text(f"SELECT c.* {_SCOPE} AND c.id = ANY(:ids)"), {**p, "ids": ids})
                         ).mappings().all()
        if not rows:
            if unreadable:
                return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.PAGE_UNREADABLE,
                                  question=QUESTION_TEMPLATES[ReasonCode.PAGE_UNREADABLE].format(page=unreadable[0])))
            return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.CLAUSE_NOT_FOUND,
                              question=QUESTION_TEMPLATES[ReasonCode.CLAUSE_NOT_FOUND]))
        return await self._semantic(claim, rows[:SEMANTIC_TOP + 1], ctx)

    # ---- numeric path ---------------------------------------------------------------------------------------------

    def _numeric(self, claim: Claim, sv: ExtractedValue, terms: list[Any], ctx: EngineContext) -> Outcome | None:
        th = ctx.thresholds
        spoken_unit = UNIT_OF.get("months" if sv.quantity == "years" else sv.quantity)
        results: list[tuple[str, Any, Decimal | None, str]] = []  # (outcome, term, inr_impact, note)
        for t in terms:
            doc = Decimal(t["value"])
            if sv.value is not None and sv.value == 0:  # "no charge" compares in any unit
                results.append(("match" if doc == 0 else "conflict", t, None, "zero"))
                continue
            if t["unit"] != spoken_unit:
                results.append(("calc", t, None, "units"))
                continue
            outcome: str
            if sv.value_low is not None and sv.value_high is not None:  # spoken range
                inside = sv.value_low <= doc <= sv.value_high
                outcome = "match" if inside else compare(sv.quantity, claim.claim_type.value,
                                                         min((sv.value_low, sv.value_high), key=lambda x: abs(x - doc)),
                                                         doc, "eq")[0]
            else:
                assert sv.value is not None
                outcome = compare(sv.quantity, claim.claim_type.value, sv.value, doc, sv.comparator)[0]
            impact = abs(sv.value - doc) if sv.value is not None and t["unit"] == "INR" else None
            note = ""
            if outcome == "match" and claim.claim_type.value == "processing_fee":
                if sv.includes_gst is True and t["includes_gst"] is False:
                    outcome, note = "gst", "spoken inclusive, document plus GST"
                elif t["includes_gst"] is False:
                    note = "plus GST"
            if outcome == "match" and claim.claim_type.value == "interest_rate" and sv.rate_basis in ("flat", "reducing"):
                bases = {b for b in (tt["basis"] for tt in terms) if b in ("flat", "reducing")}
                if bases and sv.rate_basis not in bases:
                    outcome, note = "conflict", f"basis: said {sv.rate_basis}, document {sorted(bases)[0]}"
            results.append((outcome, t, impact, note))
        if not results:
            return None
        kinds = [r[0] for r in results]
        conditional = bool(claim.conditions) or claim.utterance_kind.value == "conditional"

        def ev(rows: list[Any]) -> list[DocumentEvidence]:
            return [doc_evidence(r) for r in rows]

        def gated(status: Status, row: Any, **kw: Any) -> Outcome:
            if not ocr_ok(row, True, th.T_ocr, th.T_ocr_num):
                return Outcome(_v(Status.UNCLEAR, ReasonCode.LOW_OCR_CONFIDENCE, ev([row]),
                                  question=QUESTION_TEMPLATES[ReasonCode.LOW_OCR_CONFIDENCE].format(page=row["page_start"])))
            return Outcome(_v(status, None, ev([row]), **kw))

        if "match" in kinds and "conflict" not in kinds:
            row = next(r[1] for r in results if r[0] == "match")
            note = next((r[3] for r in results if r[0] == "match" and r[3]), "")
            return gated(Status.MATCH, row, assumptions=[note] if note == "plus GST" else [])
        if "conflict" in kinds:
            r = next(r for r in results if r[0] == "conflict")
            if conditional:
                return Outcome(_v(Status.UNCLEAR, ReasonCode.CONDITION_MISMATCH, ev([r[1]]),
                                  question=QUESTION_TEMPLATES[ReasonCode.CONDITION_MISMATCH]))
            o = gated(Status.CONFLICT, r[1], assumptions=[r[3]] if r[3].startswith("basis") else [])
            o.inr_impact = r[2]
            return o
        if "gst" in kinds:
            row = next(r[1] for r in results if r[0] == "gst")
            return Outcome(_v(Status.UNCLEAR, ReasonCode.ASSUMPTION_DEPENDENT, ev([row]),
                              question=QUESTION_TEMPLATES[ReasonCode.ASSUMPTION_DEPENDENT],
                              assumptions=["GST rate 18% (A11, confirmed at HC-5)"]))
        if "rounding" in kinds:
            row = next(r[1] for r in results if r[0] == "rounding")
            return Outcome(_v(Status.UNCLEAR, ReasonCode.POSSIBLE_ROUNDING, ev([row]),
                              question=QUESTION_TEMPLATES[ReasonCode.POSSIBLE_ROUNDING]))
        row = results[0][1]  # only unit mismatches: percent ↔ INR needs the loan amount (doc 06 formula)
        return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.CALCULATION_NOT_ENABLED, ev([row])))

    # ---- semantic path --------------------------------------------------------------------------------------------

    async def _semantic(self, claim: Claim, rows: list[Any], ctx: EngineContext) -> Outcome:
        if self.llm is None or self.nli is None:
            return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.ENGINE_NOT_APPLICABLE))
        refs = {f"C{i + 1}": r for i, r in enumerate(rows)}
        statement = claim.normalized_statement or claim.quote
        body = "Statement: " + redact(statement) + "\n\nClauses:\n" + "\n".join(
            f"{k}: {redact(r['text'].replace(chr(10), ' '))}" for k, r in refs.items())
        try:
            j = await self.llm.structured(system=JUDGE_PROMPT, messages=[{"role": "user", "content": body}],
                                          schema=JudgeOut, temperature=0.0, max_tokens=400, timeout_s=25)
        except SchemaError:
            return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.ENGINE_ERROR))
        assert isinstance(j, JudgeOut)
        row = refs.get(j.clause.strip())
        judge = j.label
        if judge != "neutral" and (row is None or not j.span.strip() or
                                   re.sub(r"\s+", " ", j.span).strip().lower() not in
                                   re.sub(r"\s+", " ", row["text"]).lower()):
            judge = "neutral"  # unverifiable span → the judge's label is not used
        row = row or rows[0]
        probs = await self.nli.score(row["text"], statement)
        t_contra = ctx.thresholds.T_nli_contra if ctx.thresholds.T_nli_contra is not None else 0.5
        t_entail = ctx.thresholds.T_nli_entail if ctx.thresholds.T_nli_entail is not None else 0.5
        nli = ("contradicts" if probs.get("contradiction", 0) >= t_contra else
               "entails" if probs.get("entailment", 0) >= t_entail else "neutral")
        log.info("contract.semantic", claim_id=str(claim.id), judge=judge, nli=nli)
        if not ocr_ok(row, False, ctx.thresholds.T_ocr, ctx.thresholds.T_ocr_num) and judge == nli != "neutral":
            return Outcome(_v(Status.UNCLEAR, ReasonCode.LOW_OCR_CONFIDENCE, [doc_evidence(row)],
                              question=QUESTION_TEMPLATES[ReasonCode.LOW_OCR_CONFIDENCE].format(page=row["page_start"])))
        if judge == nli == "contradicts":
            if bool(claim.conditions) or claim.utterance_kind.value == "conditional":
                return Outcome(_v(Status.UNCLEAR, ReasonCode.CONDITION_MISMATCH, [doc_evidence(row)],
                                  question=QUESTION_TEMPLATES[ReasonCode.CONDITION_MISMATCH]))
            return Outcome(_v(Status.CONFLICT, None, [doc_evidence(row)]))
        if judge == nli == "entails":
            return Outcome(_v(Status.MATCH, None, [doc_evidence(row)]))
        if judge == nli == "neutral":
            return Outcome(_v(Status.NOT_VERIFIED, ReasonCode.CLAUSE_NOT_FOUND,
                              question=QUESTION_TEMPLATES[ReasonCode.CLAUSE_NOT_FOUND]))
        return Outcome(_v(Status.UNCLEAR, ReasonCode.SEMANTIC_DISAGREEMENT, [doc_evidence(row)],
                          question=QUESTION_TEMPLATES[ReasonCode.SEMANTIC_DISAGREEMENT]))


__all__ = ["ContractEngine", "doc_evidence"]
