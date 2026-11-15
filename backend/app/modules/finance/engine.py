"""Finance engine — doc 06 §6, doc 08 §6 (T3.2). Checks emi / total_cost / net_disbursement claims.

- With a ready document: compute the figure from the document's terms (principal, rate + basis, tenure, fee, GST,
  deductions) via the MVP formulas and compare with the spoken value (doc 06 §5 tolerances) → C-CALC-DOC. If the
  basis isn't stated both bases are computed: matching only one → UNCLEAR(assumption_dependent).
- Without a document: the agent's own spoken P, R, n (calculation_internal) — consistent → NOT_VERIFIED(no_document)
  with the note "The agent's numbers are consistent with each other"; inconsistent → UNCLEAR(internal_inconsistency).
- Any input with gate_ok=False caps the verdict at UNCLEAR with that gate's reason.
- Every computation is stored as a trace (`enabled` = finance user-visible). **Before HC-5 (flag off) the verdict is
  NOT_VERIFIED(calculation_not_enabled)**; nothing finance-derived reaches the user.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text

from app.modules.contract.engine import doc_evidence
from app.modules.finance import formulas as F
from app.modules.finance import trace
from app.modules.finance.sourced import GST_RATE_PCT, LoanInputs, Sourced, SourcedFactory
from app.modules.finance.tolerance import compare
from app.modules.verify.merge import Outcome
from app.modules.verify.service import EngineContext, visibility
from app.schemas.claims import Claim
from app.schemas.verification import CalculationEvidence, EngineVerdict, FindingKind, ReasonCode, Status
from app.schemas.wording import REASON_TEXT

FINANCE_TYPES = {"emi", "total_cost", "net_disbursement"}
OUTPUT = {"emi": "emi", "total_cost": "total_repayment", "net_disbursement": "net_disbursement"}
GATE_CODE = {"low_asr_confidence": ReasonCode.LOW_ASR_CONFIDENCE, "speaker_unconfirmed": ReasonCode.SPEAKER_UNCONFIRMED,
             "vague_claim": ReasonCode.VAGUE_CLAIM, "low_ocr_confidence": ReasonCode.LOW_OCR_CONFIDENCE}
CONSISTENT_NOTE = "The agent's numbers are consistent with each other."
_SCOPE = ("FROM doc_terms t JOIN clauses c ON c.id = t.clause_id AND c.version = t.version "
          "JOIN documents d ON d.id = c.document_id AND d.version = c.version JOIN sessions s ON s.id = d.session_id "
          "WHERE d.session_id = :s AND s.owner_id = :u AND d.status = 'ready' AND d.deleted_at IS NULL")


def _nv(code: ReasonCode, reason: str | None = None, missing: list[str] | None = None) -> EngineVerdict:
    return EngineVerdict(engine="finance", status=Status.NOT_VERIFIED, reason_code=code, finding_kind=None,
                         reason=reason or REASON_TEXT[code], evidence=[], missing=missing or [])


def _compute(field: str, inp: LoanInputs, basis: str | None = None) -> F.Result:
    if field == "emi":
        return F.emi(inp, basis)
    if field == "total_cost":
        return F.total_repayment(inp, basis)
    return F.net_disbursement(inp)


class FinanceEngine:
    name: Literal["finance"] = "finance"
    shadow = False

    def __init__(self, visible: Callable[[], bool] | None = None) -> None:
        self.visible = visible or (lambda: visibility()[0])

    def applies(self, claim: Claim) -> bool:
        return claim.claim_type.value in FINANCE_TYPES

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome:
        visible = self.visible()
        fac = SourcedFactory(ctx.thresholds.T_asr_num, ctx.thresholds.T_ocr, ctx.thresholds.T_ocr_num)
        vals = [v for v in claim.values if v.normaliser_version and v.value is not None]
        if not vals:
            return Outcome(_nv(ReasonCode.MISSING_INPUTS, missing=["the amount the agent said"]))
        spoken = fac.from_claim_value(vals[0], claim)
        field = claim.claim_type.value
        loaded = await load_document_inputs(ctx.conn, ctx.session_id, ctx.owner_id, fac)
        if loaded is None:
            return await self._internal(claim, spoken, fac, ctx, visible)
        inp, clause_of = loaded
        if field != "net_disbursement" and inp.rate_basis == "unspecified":
            results = {b: _compute(field, inp, b) for b in ("reducing", "flat")}
        else:
            results = {inp.rate_basis: _compute(field, inp)}
        first = next(iter(results.values()))
        inputs = {k: v for k, v in (("principal", inp.principal), ("annual_rate_pct", inp.annual_rate_pct),
                                    ("tenure_months", inp.tenure_months), ("processing_fee_pct", inp.processing_fee_pct),
                                    ("processing_fee_abs", inp.processing_fee_abs), ("gst_rate_pct", inp.gst_rate_pct))
                  if v is not None} | {f"deduction_{i}": d for i, d in enumerate(inp.other_upfront_deductions)}
        if not first.ok:
            await trace.record(ctx.conn, ctx.session_id, first, inputs, tolerance_row="", enabled=visible)
            if not visible:
                return Outcome(_nv(ReasonCode.CALCULATION_NOT_ENABLED))
            return Outcome(_nv(ReasonCode.MISSING_INPUTS, missing=first.missing))
        quantity = "amount_inr"
        outcomes = {}
        for b, r in results.items():
            got, row = compare(quantity, field, spoken.value, r.outputs[OUTPUT[field]], vals[0].comparator)
            outcomes[b] = (got, row, r)
        b0, (got0, row0, r0) = next(iter(outcomes.items()))
        cid = await trace.record(ctx.conn, ctx.session_id, r0, {**inputs, "spoken": spoken}, tolerance_row=row0,
                                 enabled=visible)
        if not visible:
            return Outcome(_nv(ReasonCode.CALCULATION_NOT_ENABLED))
        docs = [doc_evidence(clause_of[s.ref_id]) for s in inputs.values() if s.source == "document"]
        docs = list({d.clause_id: d for d in docs}.values())
        calc = CalculationEvidence(calculation_id=cid, formula_id=r0.formula_id, formula_version=r0.formula_version,
                                   input_sources=sorted({s.source for s in inputs.values()} | {spoken.source}),  # type: ignore[arg-type]
                                   summary=f"From the document: {OUTPUT[field].replace('_', ' ')} = ₹{r0.outputs[OUTPUT[field]]}"
                                           f"; the agent said ₹{spoken.value}.")
        evidence: list[Any] = [calc, *docs]
        gate = sorted(set(inp.gate_reasons()) | set(spoken.gate_reasons))
        kinds = {got for got, _, _ in outcomes.values()}
        if len(outcomes) > 1 and len(kinds) > 1 and "match" in kinds:
            v = EngineVerdict(engine="finance", status=Status.UNCLEAR, reason_code=ReasonCode.ASSUMPTION_DEPENDENT,
                              finding_kind=FindingKind.CONTRACTUAL, reason=REASON_TEXT[ReasonCode.ASSUMPTION_DEPENDENT],
                              evidence=evidence, assumptions=[f"matches only if the rate is {b}" for b, (g, _, _) in
                                                               outcomes.items() if g == "match"])
            return Outcome(v)
        status = {"match": Status.MATCH, "conflict": Status.CONFLICT, "rounding": Status.UNCLEAR}[got0]
        if gate and status in (Status.MATCH, Status.CONFLICT):
            code = GATE_CODE.get(gate[0], ReasonCode.LOW_ASR_CONFIDENCE)
            return Outcome(EngineVerdict(engine="finance", status=Status.UNCLEAR, reason_code=code,
                                         finding_kind=FindingKind.CONTRACTUAL, reason=REASON_TEXT[code], evidence=evidence))
        rcode = ReasonCode.POSSIBLE_ROUNDING if status == Status.UNCLEAR else None
        v = EngineVerdict(engine="finance", status=status, reason_code=rcode, finding_kind=FindingKind.CONTRACTUAL,
                          reason=calc.summary, evidence=evidence, assumptions=r0.assumptions)
        impact = abs(spoken.value - r0.outputs[OUTPUT[field]])
        return Outcome(v, inr_impact=impact * (int(inp.tenure_months.value) if field == "emi" and inp.tenure_months else 1))

    @staticmethod
    def _doc_inputs(rows: list[Any], fac: SourcedFactory) -> tuple[LoanInputs, dict[str, Any]]:
        first: dict[str, Any] = {}
        deductions: dict[str, Any] = {}
        for r in rows:
            if r["value"] is None:
                continue
            first.setdefault(r["field"], r)
            if r["field"] in ("insurance_bundled", "other_charge") and "deduct" in (r["clause_text"] or "").lower() \
                    and r["unit"] == "INR":
                deductions.setdefault(r["field"] + (r["value_raw"] or ""), r)
        clause_of: dict[str, Any] = {}

        def s(field: str) -> Sourced | None:
            r = first.get(field)
            if r is None:
                return None
            clause_of[str(r["id"])] = _clause(r)
            return fac.from_doc_term(r, _clause(r))

        fee = first.get("processing_fee")
        rate = first.get("interest_rate")
        basis = next((r["basis"] for r in rows if r["field"] == "interest_rate" and r["basis"] in ("flat", "reducing")),
                     "unspecified")
        if any(r["field"] == "interest_rate" and r["basis"] == "flat" for r in rows):
            basis = "flat"  # small print that states a flat basis governs (AG04/AG08 trap)
        ded = []
        for r in deductions.values():
            clause_of[str(r["id"])] = _clause(r)
            ded.append(fac.from_doc_term(r, _clause(r)))
        inp = LoanInputs(
            principal=s("loan_amount"), annual_rate_pct=s("interest_rate") if rate is not None else None,
            rate_basis=basis, tenure_months=s("tenure"),
            processing_fee_pct=s("processing_fee") if fee is not None and fee["unit"] == "%" else None,
            processing_fee_abs=s("processing_fee") if fee is not None and fee["unit"] == "INR" else None,
            fee_includes_gst=bool(fee is not None and fee["includes_gst"]),
            gst_rate_pct=fac.assumption("gst_rate_pct", GST_RATE_PCT, "A11: 18% GST on fees (confirmed at HC-5)"),
            other_upfront_deductions=ded)
        return inp, clause_of

    async def _internal(self, claim: Claim, spoken: Sourced, fac: SourcedFactory, ctx: EngineContext,
                        visible: bool) -> Outcome:
        """No document: is the agent's EMI consistent with the agent's own P, R, n? (calculation_internal)."""
        if claim.claim_type.value != "emi":
            return Outcome(_nv(ReasonCode.NO_DOCUMENT))
        from app.modules.claims.service import load_claims

        others = {c.claim_type.value: c for c in await load_claims(ctx.conn, ctx.session_id)
                  if c.state == "active" and c.speaker_role != "customer" and c.values
                  and c.values[0].normaliser_version and c.values[0].value is not None}
        need = ("loan_amount", "interest_rate", "tenure")
        if not all(k in others for k in need):
            return Outcome(_nv(ReasonCode.NO_DOCUMENT))
        src = {k: fac.from_claim_value(others[k].values[0], others[k]) for k in need}
        basis = others["interest_rate"].values[0].rate_basis or "unspecified"
        inp = LoanInputs(principal=src["loan_amount"], annual_rate_pct=src["interest_rate"],
                         tenure_months=src["tenure"], rate_basis=basis if basis in ("flat", "reducing") else "unspecified")
        r = F.emi(inp)
        got, row = compare("amount_inr", "emi", spoken.value, r.outputs["emi"])
        if basis not in ("flat", "reducing") and got != "match":
            alt = F.emi(inp, "flat")
            if compare("amount_inr", "emi", spoken.value, alt.outputs["emi"])[0] == "match":
                got, r = "match", alt
        await trace.record(ctx.conn, ctx.session_id, r, {**src, "spoken": spoken}, tolerance_row=row, enabled=visible)
        if not visible:
            return Outcome(_nv(ReasonCode.CALCULATION_NOT_ENABLED))
        if got == "match":
            return Outcome(_nv(ReasonCode.NO_DOCUMENT, reason=f"{REASON_TEXT[ReasonCode.NO_DOCUMENT]} {CONSISTENT_NOTE}"))
        return Outcome(EngineVerdict(engine="finance", status=Status.UNCLEAR, reason_code=ReasonCode.INTERNAL_INCONSISTENCY,
                                     finding_kind=FindingKind.CALCULATION_INTERNAL,
                                     reason=REASON_TEXT[ReasonCode.INTERNAL_INCONSISTENCY], evidence=[],
                                     suggested_question="Ask the agent to confirm the EMI in writing."))


async def load_document_inputs(conn: Any, session_id: Any, owner_id: Any, fac: SourcedFactory
                               ) -> tuple[LoanInputs, dict[str, Any]] | None:
    """Factory-built LoanInputs from the current versions of the session's ready documents (None: no document terms).
    Shared by the finance engine and the Ask finance route; `clause_of` maps doc_term id → its clause row."""
    rows = (await conn.execute(text(
        f"SELECT t.*, c.text AS clause_text, c.text_source, c.ocr_conf, c.ocr_min_numeric_conf, c.page_start, "
        f"c.page_end, c.bboxes, c.document_id AS c_document_id, c.version AS c_version, c.id AS c_id {_SCOPE} "
        "ORDER BY c.ord"), {"s": session_id, "u": owner_id})).mappings().all()
    if not rows:
        return None
    return FinanceEngine._doc_inputs(list(rows), fac)


def _clause(r: Any) -> dict[str, Any]:
    return {"id": r["c_id"], "document_id": r["c_document_id"], "version": r["c_version"], "page_start": r["page_start"],
            "bboxes": r["bboxes"], "text": r["clause_text"], "ocr_conf": r["ocr_conf"],
            "ocr_min_numeric_conf": r["ocr_min_numeric_conf"], "text_source": r["text_source"]}


__all__ = ["FinanceEngine", "Decimal"]
