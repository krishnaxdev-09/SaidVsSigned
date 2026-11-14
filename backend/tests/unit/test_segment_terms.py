"""T2.3: clause segmentation rules (doc 05 §6.6) and doc_terms (doc 05 §6.7; values only from the normaliser)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.retry import SchemaError
from app.llm.fakes import FakeLLMProvider
from app.modules.documents import segment, terms
from app.modules.documents.parse import Item

B = (0.1, 0.1, 0.9, 0.12)


def t(label: str, text: str, page: int = 1, **kw: object) -> Item:
    return Item(f"#/{label}/{text[:8]}", page, label, text, B, **kw)  # type: ignore[arg-type]


def cell(r: int, c: int, text: str, header: bool = False, table: str = "#/tables/0") -> Item:
    return Item(f"{table}/r{r}c{c}", 1, "table_cell", text, B, table={"table": table, "row": r, "col": c, "header": header})


def test_new_clause_at_header_list_item_numbering_footnote_and_signature() -> None:
    cl = segment.segment([
        t("page_header", "Lender — page 1"),
        t("section_header", "Terms and Conditions"),
        t("list_item", "1. Loan. The lender agrees to lend."),
        t("text", "This continues clause 1."),
        t("text", "2.1 Interest is payable monthly."),
        t("text", "(a) on the due date;"),
        t("footnote", "* Flat rate applies."),
        t("text", "Signed: ______"),
        t("page_footer", "Page 1 of 2"),
    ])
    assert [c.label for c in cl] == ["list_item", "text", "text", "footnote", "signature"]
    assert cl[0].text == "1. Loan. The lender agrees to lend.\nThis continues clause 1."
    assert all(c.section_path == "Terms and Conditions" for c in cl) and [c.ord for c in cl] == [0, 1, 2, 3, 4]


def test_header_with_body_is_a_clause_header_only_is_not() -> None:
    cl = segment.segment([t("section_header", "Lender Ltd"), t("section_header", "Key Fact Statement"),
                          t("text", "Agreement No. 7"), t("section_header", "Schedule"), t("list_item", "1. Fees")])
    assert [c.text for c in cl] == ["Key Fact Statement\nAgreement No. 7", "1. Fees"]
    assert cl[1].section_path == "Schedule"


def test_each_table_row_is_a_clause_with_the_header_prepended() -> None:
    cl = segment.segment([t("section_header", "KFS"), cell(0, 1, "Details", True), cell(0, 0, "Item", True),
                          cell(1, 0, "Loan amount"), cell(1, 1, "Rs. 5,00,000"), cell(2, 0, "Tenure"), cell(2, 1, "48 months"),
                          t("text", "Note: subject to KYC.")])
    assert [c.text for c in cl] == ["Item | Details\nLoan amount | Rs. 5,00,000", "Item | Details\nTenure | 48 months",
                                    "Note: subject to KYC."]
    assert cl[0].is_table_row and cl[0].cells == ["Loan amount", "Rs. 5,00,000"] and cl[0].section_path == "KFS"
    assert not cl[2].is_table_row  # a paragraph after a table starts a new clause


def test_no_header_is_assumed_when_none_is_flagged() -> None:
    cl = segment.segment([cell(0, 0, "Loan amount"), cell(0, 1, "Rs. 1,00,000")])
    assert [c.text for c in cl] == ["Loan amount | Rs. 1,00,000"]


def test_a_clause_can_span_pages() -> None:
    cl = segment.segment([t("list_item", "3. Default. On delay", 1), t("text", "penal interest applies.", 2)])
    assert (cl[0].page_start, cl[0].page_end, [b["page"] for b in cl[0].bboxes]) == (1, 2, [1, 2])


def test_clause_confidence() -> None:
    a = t("text", "Fee 2%", text_source="ocr", ocr_conf=0.9, ocr_min_numeric_conf=0.8)
    b = t("text", "plus GST", text_source="ocr", ocr_conf=0.7, ocr_min_numeric_conf=0.7)
    c = t("text", "EMI 500", text_source="ocr", ocr_conf=None, ocr_min_numeric_conf=None)
    one = segment.Clause(0, 1, 1, "text", "", None, items=[a, b])
    assert one.text_source == "ocr" and one.ocr_conf == pytest.approx(0.8) and one.ocr_min_numeric_conf == 0.8
    assert segment.Clause(0, 1, 1, "text", "", None, items=[a, c]).ocr_min_numeric_conf is None  # unknown → gate low


@pytest.mark.parametrize(("label", "field"), [
    ("Loan amount", "loan_amount"), ("Sanctioned Amount", "loan_amount"), ("Net disbursed amount", "net_disbursement"),
    ("Rate of interest", "interest_rate"), ("Tenure", "tenure"), ("EMI", "emi"), ("Processing fee", "processing_fee"),
    ("Insurance", "insurance_bundled"), ("Total amount payable", "total_cost"), ("Cheque/ECS bounce", "other_charge"),
    ("Foreclosure charges", "prepayment_charge"), ("Penal charges", "penal_charge"), ("Governing law", None),
])
def test_lexicon_labels(label: str, field: str | None) -> None:
    assert terms.lexicon_field(label) == field


def _row(label: str, value: str) -> segment.Clause:
    return segment.segment([cell(0, 0, label), cell(0, 1, value)])[0]


def test_lexicon_values_come_from_the_normaliser() -> None:
    rows = [_row("Loan amount", "Rs. 10,00,000"), _row("Rate of interest", "17.50% p.a. (reducing, fixed)"),
            _row("Tenure", "3 years"), _row("Processing fee", "Rs. 4,999 (incl. GST)"), _row("Processing fee", "2% + GST"),
            _row("Insurance", "as applicable")]
    for n, c in enumerate(rows):
        c.ord = n
    got = {(x.clause_ord): x for x in terms.lexicon_terms(rows)}
    assert (got[0].value, got[0].unit) == (Decimal(1000000), "INR")
    assert (got[1].value, got[1].unit, got[1].basis, got[1].rate_type) == (Decimal("17.5"), "%", "reducing", "fixed")
    assert (got[2].value, got[2].unit) == (Decimal(36), "months")
    assert (got[3].value, got[3].unit, got[3].includes_gst) == (Decimal(4999), "INR", True)
    assert (got[4].value, got[4].unit, got[4].includes_gst) == (Decimal(2), "%", False)
    assert got[5].value is None and got[5].normaliser_version is None  # unparsed → None, never guessed
    assert all(x.source == "lexicon" for x in got.values())


def test_label_value_line_outside_a_table() -> None:
    c = segment.segment([t("text", "Processing Fee: 1.5% of the loan amount")])[0]
    (term,) = terms.lexicon_terms([c])
    assert (term.field, term.value, term.unit) == ("processing_fee", Decimal("1.5"), "%")


async def test_llm_pass_keeps_only_verbatim_terms_and_never_takes_values() -> None:
    cl = segment.segment([t("list_item", "1. Prepayment attracts 4% of principal outstanding after 6 EMIs. "
                                        "Contact PAN ABCPE1234F."),
                          t("list_item", "2. Governing law: India."), cell(0, 0, "EMI"), cell(0, 1, "Rs. 500")])
    llm = FakeLLMProvider([{"terms": [
        {"clause": "C1", "field": "prepayment_charge", "value_raw": "4%  of principal\noutstanding", "quantity": "percent",
         "conditions": ["after 6 EMIs"]},
        {"clause": "C1", "field": "prepayment_charge", "value_raw": "4% of principal outstanding", "quantity": "percent"},
        {"clause": "C1", "field": "penal_charge", "value_raw": "2% per month", "quantity": "percent"},
        {"clause": "C1", "field": "other_charge", "value_raw": "[PAN]", "quantity": "amount_inr"},
        {"clause": "C7", "field": "emi", "value_raw": "Rs. 500", "quantity": "amount_inr"}]}])
    covered = {x.clause_ord for x in terms.lexicon_terms(cl)}
    assert [c.ord for c in terms.llm_candidates(cl, covered)] == [0]  # no number in clause 2; EMI row is lexicon
    out, stats = await terms.llm_terms(llm, cl, covered)
    assert [(x.field, x.value, x.unit, x.source, x.conditions) for x in out] == [
        ("prepayment_charge", Decimal(4), "%", "llm", ["after 6 EMIs"])]
    assert (stats["dropped_not_verbatim"], stats["dropped_placeholder"], stats["dropped_ref"],
            stats["dropped_duplicate"]) == (1, 1, 1, 1)
    assert llm.calls == 1


async def test_llm_prompt_is_redacted_and_schema_failure_is_skipped() -> None:
    cl = segment.segment([t("list_item", "1. Fee 2%. PAN ABCPE1234F.")])
    seen: list[str] = []

    class Spy:
        name = "spy"

        async def structured(self, *, messages: list[dict], **kw: object) -> object:
            seen.append(messages[-1]["content"])
            raise SchemaError("invalid after corrective retry")

    out, stats = await terms.llm_terms(Spy(), cl, set())  # type: ignore[arg-type]
    assert out == [] and stats["schema_failed"] == 1
    assert "ABCPE1234F" not in seen[0] and "[PAN]" in seen[0]


def test_merged_layout_block_is_split_and_mid_sentence_items_continue() -> None:
    """Docling merged clauses 2–6 + the signature into one list item on AG04 (T2.3 evaluation)."""
    cl = segment.segment([
        t("section_header", "Terms and Conditions", 2),
        t("list_item", "1. Loan. The Lender agrees to lend Rs. 60,000 on the terms in the Key", 2),
        t("list_item", "Fact Statement, which forms part of this Agreement. 2. Interest. Payable monthly. "
                       "3. Repayment. In 12 EMIs at 2.5 p.a. on the 5th. Signed: ____ (Borrower)", 2),
    ])
    assert [c.text.split(".")[0] for c in cl] == ["1", "2", "3", "Signed: ____ (Borrower)"]
    assert cl[0].text.endswith("Key\nFact Statement, which forms part of this Agreement.")
    assert cl[-1].label == "signature" and cl[1].bboxes == cl[2].bboxes  # pieces keep the merged block's bbox
