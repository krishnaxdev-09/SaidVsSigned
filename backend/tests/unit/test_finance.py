"""T3.1 finance engine (doc 06): golden table, agreements, UnvalidatedInputError, properties, gates, missing inputs."""

from __future__ import annotations

import uuid
from decimal import Decimal as D
from pathlib import Path

import numpy_financial as npf
import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.finance import formulas as F
from app.modules.finance.sourced import LoanInputs, Sourced, SourcedFactory, UnvalidatedInputError, require
from tests.unit.test_verify_merge import claim

FAC = SourcedFactory()
GOLDEN = yaml.safe_load((Path(__file__).parents[1] / "golden" / "finance.yaml").read_text())["cases"]
AGREEMENTS = Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements"


def doc(value: str | D, vid: str = "t", text_source: str = "text_layer", ocr: float | None = 1.0) -> Sourced:
    return FAC.from_doc_term({"id": vid, "value": D(str(value)), "value_raw": str(value), "normaliser_version": "norm-1.0"},
                             {"text_source": text_source, "ocr_conf": ocr, "ocr_min_numeric_conf": ocr})


def inputs(p: D | str, rate: D | str, n: int, basis: str = "reducing", **kw) -> LoanInputs:  # type: ignore[no-untyped-def]
    return LoanInputs(principal=doc(p), annual_rate_pct=doc(rate), tenure_months=doc(n), rate_basis=basis, **kw)


def test_golden_table_100_percent() -> None:
    assert len(GOLDEN) >= 60
    for c in GOLDEN:
        inp = inputs(c["principal"], c["rate"], c["tenure"], c["basis"])
        out = F.total_repayment(inp).outputs
        assert abs(out["emi"] - D(c["emi"])) <= D("0.01"), c["id"]
        assert abs(out["total_repayment"] - D(c["total_repayment"])) <= D(1), c["id"]
        if c["basis"] == "reducing" and D(c["rate"]) > 0:  # independent cross-check (tests only)
            assert abs(D(c["emi"]) - D(str(round(-npf.pmt(float(c["rate"]) / 1200, c["tenure"], float(c["principal"])), 2)))) \
                <= D("0.01")


@pytest.mark.parametrize("ag", [f"AG{i:02d}" for i in range(1, 11)])
def test_agreement_figures(ag: str) -> None:
    t = yaml.safe_load((AGREEMENTS / ag / "terms.yaml").read_text())
    first = {}
    for term in t["terms"]:
        first.setdefault(term["field"], term)
    basis = first["interest_rate"].get("rate_basis", "reducing")
    if any(x["field"] == "interest_rate" and x.get("rate_basis") == "flat" for x in t["terms"]):
        basis = "flat"
    fee = first.get("processing_fee")
    inp = inputs(first["loan_amount"]["value"], first["interest_rate"]["value"], int(first["tenure"]["value"]), basis,
                 gst_rate_pct=FAC.assumption("gst_rate_pct", D(18), "A11"),
                 processing_fee_pct=doc(fee["value"]) if fee and fee["quantity"] == "percent" else None,
                 processing_fee_abs=doc(fee["value"]) if fee and fee["quantity"] == "amount_inr" else None,
                 fee_includes_gst=bool(fee and fee.get("includes_gst")),
                 other_upfront_deductions=[doc(x["value"]) for x in  # each deducted charge once (KFS row + clause repeat it)
                                           {x["field"]: x for x in t["terms"] if x.get("deducted_from_disbursement")}.values()])
    e = F.emi(inp).outputs["emi"]
    assert abs(e - D(str(first["emi"]["value"]))) <= D(1)
    rupee = e.quantize(D(1), rounding="ROUND_HALF_UP")
    assert rupee * int(first["tenure"]["value"]) == D(str(first["total_cost"]["value"]))
    net = F.net_disbursement(inp).outputs["net_disbursement"]
    assert abs(net - D(str(first["net_disbursement"]["value"]))) <= D(1)


@pytest.mark.parametrize("bad", [{"value": 1}, 1.5, D(1), "100000"])
def test_unvalidated_inputs_are_refused(bad: object) -> None:
    with pytest.raises((UnvalidatedInputError, ValueError)):
        F.emi(LoanInputs(principal=bad, annual_rate_pct=doc(10), tenure_months=doc(12)))  # type: ignore[arg-type]


def test_hand_built_or_forged_sourced_is_refused() -> None:
    hand = Sourced(value=D(100000), source="document", ref_table="doc_terms", ref_id="x", gate_ok=True)
    with pytest.raises(UnvalidatedInputError):
        F.emi(LoanInputs(principal=hand, annual_rate_pct=doc(10), tenure_months=doc(12)))
    with pytest.raises(UnvalidatedInputError):
        require(Sourced.model_construct(value=D(1), source="document", ref_table="doc_terms", ref_id="x", gate_ok=True))
    with pytest.raises(UnvalidatedInputError):
        doc(100000).model_copy(update={"value": D(1)})
    assert require(doc(5).model_copy()).value == 5  # a plain copy is still the same validated value
    with pytest.raises(UnvalidatedInputError):  # an unparsed value never becomes an input
        FAC.from_doc_term({"id": "t", "value": None, "value_raw": "as applicable", "normaliser_version": None},
                          {"text_source": "text_layer", "ocr_conf": 1, "ocr_min_numeric_conf": 1})


def test_gates_recorded_on_inputs() -> None:
    from app.schemas.claims import ExtractedValue

    v = ExtractedValue(field="emi", raw_text="1 percent", quantity="amount_inr", value=D(9800), normaliser_version="norm-1.0")
    ok = FAC.from_claim_value(v, claim(quote_confirmed_by_user=True))
    assert ok.gate_ok and ok.source == "agent_said"
    bad = FAC.from_claim_value(v, claim(role_assignment="auto", quote_confirmed_by_user=False, asr_min_numeric_conf=0.5))
    assert not bad.gate_ok and bad.gate_reasons == ["speaker_unconfirmed", "low_asr_confidence"]
    assert doc(5, text_source="ocr", ocr=0.5).gate_reasons == ["low_ocr_confidence"]
    inp = inputs(100000, 12, 12)
    inp.emi = bad
    assert inp.gate_reasons() == ["low_asr_confidence", "speaker_unconfirmed"]


def test_missing_inputs_and_assumptions() -> None:
    r = F.emi(LoanInputs(principal=doc(100000)))
    assert not r.ok and r.missing == ["annual_rate_pct", "tenure_months"] and r.outputs == {}
    r = F.emi(inputs(100000, 12, 12, "unspecified"))
    assert r.assumptions and r.formula_id == "emi_reducing_v1"
    g = F.gst_on_fee(LoanInputs(principal=doc(100000), processing_fee_pct=doc(2)))
    assert g.outputs == {"fee": D("2000.00"), "gst": D("360.00")} and "assumed" in g.assumptions[0]
    g = F.gst_on_fee(LoanInputs(principal=doc(100000), processing_fee_abs=doc(4999), fee_includes_gst=True))
    assert g.outputs["gst"] == 0
    assert F.fee_amount(LoanInputs(processing_fee_pct=doc(2))).missing == ["principal"]


@settings(max_examples=300, deadline=None)
@given(st.integers(10_000, 5_000_000), st.decimals("0.5", "36", places=2), st.integers(2, 360))
def test_properties(p: int, rate: D, n: int) -> None:
    red, flat = F.emi_reducing(D(p), rate, n), F.emi_flat(D(p), rate, n)
    assert red <= flat  # reducing EMI ≤ flat EMI for R > 0, n > 1
    rows = F.amortisation(D(p), rate, n)
    assert abs(rows[-1]["balance"]) <= 1 and len(rows) == n  # the last row absorbs rounding; the schedule closes
    net = F.net_disbursement(inputs(p, rate, n, processing_fee_pct=doc(2), gst_rate_pct=FAC.assumption(
        "gst_rate_pct", D(18), "A11"))).outputs["net_disbursement"]
    assert net <= p


def test_trace_rejects_unvalidated_inputs() -> None:
    import asyncio

    from app.modules.finance import trace

    hand = Sourced(value=D(1), source="document", ref_table="doc_terms", ref_id="x", gate_ok=True)
    with pytest.raises(UnvalidatedInputError):
        asyncio.run(trace.record(None, uuid.uuid4(), F.Result("emi_reducing_v1"), {"principal": hand},  # type: ignore[arg-type]
                                 tolerance_row="emi", enabled=False))
