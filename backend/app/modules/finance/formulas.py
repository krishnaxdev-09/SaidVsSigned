"""MVP formulas — doc 06 §4 (T3.1). Decimal only; the LLM never supplies numbers.

Each formula takes `LoanInputs` (factory-built) and returns a `Result` (outputs + formula id/version + missing +
assumptions). Missing inputs → `missing` filled and no outputs (the engine maps it to NOT_VERIFIED(missing_inputs)).
Gated formulas (APR, flat-to-effective, prepayment, penal) are T3.6 and stay off for the whole MVP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, getcontext

from app.modules.finance.sourced import GST_RATE_PCT, LoanInputs, SourcedFactory

getcontext().prec = 34
D = Decimal
PAISA = D("0.01")
MVP_FORMULAS = {"emi_reducing_v1", "emi_flat_v1", "total_repayment_v1", "total_interest_v1", "fee_amount_v1",
                "gst_on_fee_v1", "net_disbursement_v1", "amortisation_v1"}


def q(x: Decimal) -> Decimal:
    return x.quantize(PAISA, rounding=ROUND_HALF_UP)


@dataclass
class Result:
    formula_id: str
    outputs: dict[str, Decimal] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    formula_version: int = 1

    @property
    def ok(self) -> bool:
        return not self.missing


def _need(inp: LoanInputs, *names: str) -> list[str]:
    return [n for n in names if getattr(inp, n) is None]


# ---- pure maths (Decimal in, Decimal out) ----------------------------------------------------------------------------

def emi_reducing(p: Decimal, annual_rate_pct: Decimal, n: int) -> Decimal:
    if n <= 0:
        raise ValueError("tenure must be positive")
    r = annual_rate_pct / D(1200)
    if r == 0:
        return q(p / n)
    f = (1 + r) ** n
    return q(p * r * f / (f - 1))


def emi_flat(p: Decimal, annual_rate_pct: Decimal, n: int) -> Decimal:
    if n <= 0:
        raise ValueError("tenure must be positive")
    interest = p * annual_rate_pct / D(100) * D(n) / D(12)
    return q((p + interest) / n)


def amortisation(p: Decimal, annual_rate_pct: Decimal, n: int) -> list[dict[str, Decimal]]:
    """Reducing-balance schedule; the last row absorbs rounding so the balance closes to 0."""
    emi = emi_reducing(p, annual_rate_pct, n)
    r = annual_rate_pct / D(1200)
    bal, rows = p, []
    for k in range(1, n + 1):
        interest = q(bal * r)
        principal = emi - interest if k < n else bal
        bal = bal - principal
        rows.append({"month": D(k), "emi": interest + principal, "interest": interest, "principal": principal,
                     "balance": q(bal)})
    return rows


# ---- formulas on LoanInputs ------------------------------------------------------------------------------------------

def emi(inp: LoanInputs, basis: str | None = None) -> Result:
    inp.checked()
    b = basis or inp.rate_basis
    fid = "emi_flat_v1" if b == "flat" else "emi_reducing_v1"
    res = Result(fid, missing=_need(inp, "principal", "annual_rate_pct", "tenure_months"))
    if res.missing:
        return res
    assert inp.principal and inp.annual_rate_pct and inp.tenure_months
    p, rate, n = inp.principal.value, inp.annual_rate_pct.value, int(inp.tenure_months.value)
    if b == "unspecified":
        res.assumptions.append("rate basis not stated: reducing assumed (flat also computed)")
    res.outputs["emi"] = emi_flat(p, rate, n) if b == "flat" else emi_reducing(p, rate, n)
    return res


def total_repayment(inp: LoanInputs, basis: str | None = None) -> Result:
    e = emi(inp, basis)
    res = Result("total_repayment_v1", missing=e.missing, assumptions=e.assumptions)
    if e.ok:
        assert inp.tenure_months
        res.outputs = {"emi": e.outputs["emi"], "total_repayment": q(e.outputs["emi"] * inp.tenure_months.value)}
    return res


def total_interest(inp: LoanInputs, basis: str | None = None) -> Result:
    t = total_repayment(inp, basis)
    res = Result("total_interest_v1", missing=t.missing, assumptions=t.assumptions)
    if t.ok:
        assert inp.principal
        res.outputs = {**t.outputs, "total_interest": q(t.outputs["total_repayment"] - inp.principal.value)}
    return res


def fee_amount(inp: LoanInputs) -> Result:
    inp.checked()
    res = Result("fee_amount_v1")
    if inp.processing_fee_abs is not None:
        res.outputs["fee"] = q(inp.processing_fee_abs.value)
    elif inp.processing_fee_pct is not None:
        if inp.principal is None:
            res.missing.append("principal")
            return res
        res.outputs["fee"] = q(inp.processing_fee_pct.value * inp.principal.value / D(100))
    else:
        res.outputs["fee"] = D("0.00")
    return res


def gst_on_fee(inp: LoanInputs) -> Result:
    f = fee_amount(inp)
    res = Result("gst_on_fee_v1", missing=f.missing)
    if not f.ok:
        return res
    if inp.fee_includes_gst:
        res.outputs = {"fee": f.outputs["fee"], "gst": D("0.00")}
        res.assumptions.append("document states the fee includes GST")
        return res
    gst = inp.gst_rate_pct.value if inp.gst_rate_pct is not None else GST_RATE_PCT
    if inp.gst_rate_pct is None or inp.gst_rate_pct.source == "assumption":
        res.assumptions.append(f"GST at {gst}% on the fee (assumed, A11; confirmed at HC-5)")
    res.outputs = {"fee": f.outputs["fee"], "gst": q(f.outputs["fee"] * gst / D(100))}
    return res


def net_disbursement(inp: LoanInputs) -> Result:
    g = gst_on_fee(inp)
    res = Result("net_disbursement_v1", missing=g.missing + _need(inp, "principal"), assumptions=g.assumptions)
    if res.missing:
        return res
    assert inp.principal
    deductions = sum((d.value for d in inp.other_upfront_deductions), D(0))
    res.outputs = {"fee": g.outputs["fee"], "gst": g.outputs["gst"], "other_deductions": q(deductions),
                   "net_disbursement": q(inp.principal.value - g.outputs["fee"] - g.outputs["gst"] - deductions)}
    return res


def default_gst(factory: SourcedFactory) -> LoanInputs:  # helper for tests and the engine
    return LoanInputs(gst_rate_pct=factory.assumption("gst_rate_pct", GST_RATE_PCT, "A11: 18% GST on fees"))
