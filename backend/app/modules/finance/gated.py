"""Gated formulas — doc 06 §4 (T3.6): APR (IRR), flat-to-effective, prepayment charge, penal charge.

Built and tested, **disabled for the whole MVP** (`finance.gated_formulas_enabled=false`, enforced at startup), even
after HC-5. `run()` always returns NOT_VERIFIED(calculation_not_enabled) to callers while the flag is off; the
computed values exist only in shadow (traces with `enabled=false`) for evaluation (E7). APR annualisation is chosen at
HC-5, so both nominal (12 × monthly IRR) and effective ((1 + r)^12 − 1) are computed.

IRR: Newton with bisection fallback; |NPV| < ₹0.01; at most 200 iterations; otherwise `CalcNotConverged`
(→ NOT_VERIFIED(calc_not_converged), canonical in doc 08).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.config import get_settings, load_features
from app.modules.finance import formulas as F
from app.modules.finance.sourced import GST_RATE_PCT, LoanInputs, Sourced, require

D = Decimal
GATED = {"apr_v1", "flat_to_effective_v1", "prepayment_charge_v1", "penal_charge_v1"}
NPV_TOL = D("0.01")
MAX_ITER = 200


class CalcNotConverged(ArithmeticError):
    pass


def npv(rate: Decimal, flows: list[Decimal]) -> Decimal:
    return sum((cf / (1 + rate) ** k for k, cf in enumerate(flows)), D(0))


def irr(flows: list[Decimal]) -> Decimal:
    """Periodic IRR of `flows` (flows[0] at t=0). Newton first, bisection on [−0.99, 1] as fallback."""
    if not (any(f > 0 for f in flows) and any(f < 0 for f in flows)):
        raise CalcNotConverged("cash flows need both signs")
    r = D("0.01")
    for _ in range(MAX_ITER):
        f = npv(r, flows)
        if abs(f) < NPV_TOL:
            return r
        d = sum((-k * cf / (1 + r) ** (k + 1) for k, cf in enumerate(flows)), D(0))
        if d == 0:
            break
        nr = r - f / d
        if nr <= D("-0.99") or nr > 10:
            break
        r = nr
    lo, hi = D("-0.99"), D(1)
    flo = npv(lo, flows)
    for _ in range(MAX_ITER):
        mid = (lo + hi) / 2
        fm = npv(mid, flows)
        if abs(fm) < NPV_TOL:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    raise CalcNotConverged(f"IRR did not converge in {MAX_ITER} iterations")


@dataclass
class GatedResult:
    formula_id: str
    enabled: bool
    outputs: dict[str, Decimal] = field(default_factory=dict)  # shadow values; never shown while disabled
    missing: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    not_converged: bool = False

    @property
    def reason_code(self) -> str | None:
        if not self.enabled:
            return "calculation_not_enabled"
        if self.not_converged:
            return "calc_not_converged"
        return "missing_inputs" if self.missing else None


def gated_enabled() -> bool:
    return bool(load_features(get_settings().FEATURES_FILE).get("finance", {}).get("gated_formulas_enabled"))


def _annualise(monthly: Decimal) -> dict[str, Decimal]:
    return {"monthly_irr": monthly, "apr_nominal_pct": F.q(monthly * 1200),
            "apr_effective_pct": F.q(((1 + monthly) ** 12 - 1) * 100)}


def apr(inp: LoanInputs) -> GatedResult:
    """IRR of (+net disbursement, −EMI × n), on net not gross disbursement (doc 06 §11)."""
    net, e = F.net_disbursement(inp), F.emi(inp)
    res = GatedResult("apr_v1", enabled=False, missing=sorted(set(net.missing + e.missing)),
                      assumptions=net.assumptions + e.assumptions)
    if res.missing:
        return res
    assert inp.tenure_months
    flows = [net.outputs["net_disbursement"], *[-e.outputs["emi"]] * int(inp.tenure_months.value)]
    try:
        res.outputs = {**_annualise(irr(flows)), "net_disbursement": net.outputs["net_disbursement"], "emi": e.outputs["emi"]}
    except CalcNotConverged:
        res.not_converged = True
    return res


def flat_to_effective(inp: LoanInputs) -> GatedResult:
    """12 × monthly IRR of the flat schedule (principal in, flat EMI out)."""
    res = GatedResult("flat_to_effective_v1", enabled=False)
    e = F.emi(inp, "flat")
    res.missing = e.missing
    if res.missing:
        return res
    assert inp.principal and inp.tenure_months
    flows = [inp.principal.value, *[-e.outputs["emi"]] * int(inp.tenure_months.value)]
    try:
        res.outputs = {**_annualise(irr(flows)), "flat_emi": e.outputs["emi"]}
    except CalcNotConverged:
        res.not_converged = True
    return res


def prepayment_charge(inp: LoanInputs, pct: Sourced, after_month: int) -> GatedResult:
    """pct × outstanding principal after `after_month` EMIs, + GST (assumed 18% unless stated)."""
    require(pct, "prepayment pct")
    res = GatedResult("prepayment_charge_v1", enabled=False, missing=[n for n in ("principal", "annual_rate_pct",
                                                                                   "tenure_months") if getattr(inp, n) is None])
    if res.missing:
        return res
    assert inp.principal and inp.annual_rate_pct and inp.tenure_months
    rows = F.amortisation(inp.principal.value, inp.annual_rate_pct.value, int(inp.tenure_months.value))
    outstanding = inp.principal.value if after_month <= 0 else rows[min(after_month, len(rows)) - 1]["balance"]
    charge = F.q(outstanding * pct.value / D(100))
    gst = F.q(charge * GST_RATE_PCT / D(100))
    res.assumptions.append(f"GST at {GST_RATE_PCT}% (assumed, A11)")
    res.outputs = {"outstanding": outstanding, "charge": charge, "gst": gst, "total": charge + gst}
    return res


def penal_charge(overdue_amount: Sourced, pct_per_month: Sourced, months_overdue: Decimal) -> GatedResult:
    """Per the document's stated basis: pct per month on the overdue amount, simple (no compounding)."""
    require(overdue_amount, "overdue amount")
    require(pct_per_month, "penal pct")
    charge = F.q(overdue_amount.value * pct_per_month.value / D(100) * months_overdue)
    return GatedResult("penal_charge_v1", enabled=False, outputs={"penal_charge": charge},
                       assumptions=["simple, per month, on the overdue amount (document basis)"])


def run(fn: Callable[..., GatedResult], *args: object, enabled: bool | None = None) -> GatedResult:
    """The only entry point for callers: computes in shadow and reports `calculation_not_enabled` unless the gated
    flag is on — which the startup check refuses for the whole MVP (doc 06 §4a)."""
    res = fn(*args)
    res.enabled = gated_enabled() if enabled is None else enabled
    return res
