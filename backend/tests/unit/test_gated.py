"""T3.6 gated formulas: correct in shadow, never enabled in the MVP (doc 06 §4, §4a, §8)."""

from __future__ import annotations

from decimal import Decimal as D

import numpy_financial as npf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import BACKEND_DIR, StartupCheckError, check_features, load_features
from app.modules.finance import gated as G
from app.modules.finance.sourced import LoanInputs
from tests.unit.test_finance import FAC, doc, inputs


@settings(max_examples=150, deadline=None)
@given(st.integers(10_000, 2_000_000), st.decimals("1", "30", places=2), st.integers(3, 120))
def test_irr_matches_numpy_financial(p: int, rate: D, n: int) -> None:
    from app.modules.finance.formulas import emi_reducing

    e = emi_reducing(D(p), rate, n)
    flows = [D(p), *[-e] * n]
    ours = G.irr(flows)
    ref = npf.irr([float(x) for x in flows])
    assert abs(float(ours) - ref) < 1e-6
    assert abs(G.npv(ours, flows)) < G.NPV_TOL


def test_apr_on_net_disbursement_exceeds_the_rate_when_fees_are_deducted() -> None:
    inp = inputs(300000, "14.50", 36, processing_fee_pct=doc(2), gst_rate_pct=FAC.assumption("gst_rate_pct", D(18), "A11"))
    r = G.apr(inp)
    assert r.outputs["apr_nominal_pct"] > D("14.50") and r.outputs["apr_effective_pct"] > r.outputs["apr_nominal_pct"]
    no_fee = G.apr(inputs(300000, "14.50", 36))
    assert abs(no_fee.outputs["apr_nominal_pct"] - D("14.50")) <= D("0.01")


def test_flat_to_effective() -> None:
    r = G.flat_to_effective(inputs(60000, 11, 12, "flat"))
    assert D(19) < r.outputs["apr_nominal_pct"] < D(21)  # an 11% flat rate is ≈ 19.6% on a reducing basis


def test_prepayment_and_penal() -> None:
    r = G.prepayment_charge(inputs(2500000, "8.75", 240), doc(2), 0)
    assert r.outputs["charge"] == D("50000.00") and r.outputs["gst"] == D("9000.00")
    later = G.prepayment_charge(inputs(2500000, "8.75", 240), doc(2), 60)
    assert later.outputs["outstanding"] < D(2500000)
    assert G.penal_charge(doc(35902), doc(3), D(1)).outputs["penal_charge"] == D("1077.06")


def test_missing_and_not_converged() -> None:
    assert G.apr(LoanInputs(principal=doc(100000))).missing
    with pytest.raises(G.CalcNotConverged):
        G.irr([D(100), D(50)])


def test_gated_formulas_are_never_enabled_in_the_mvp() -> None:
    f = load_features(BACKEND_DIR / "config" / "features.yaml")
    assert f["finance"]["gated_formulas_enabled"] is False
    r = G.run(G.apr, inputs(300000, "14.50", 36))
    assert not r.enabled and r.reason_code == "calculation_not_enabled" and r.outputs  # computed in shadow only
    with pytest.raises(StartupCheckError):
        check_features({**f, "finance": {**f["finance"], "gated_formulas_enabled": True}}, real_users=False)
