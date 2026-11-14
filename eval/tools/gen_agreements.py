"""Generate the 10 synthetic loan agreements / KFS documents for T0.13 (doc 12 §A.1 item 1).

Output per agreement in eval/datasets/agreements/<id>/:
  - agreement.pdf  digital PDF (born-digital; print-and-scan and phone photos are made from it by hand)
  - terms.yaml     ground truth: every key term with the exact printed text, its page, and where it sits

All lenders, borrowers, addresses and numbers are fictional. Field names follow doc 04 §4 (ClaimType /
ExtractedValue). Printed figures come from this generator's own arithmetic; they are synthetic test data
and are NOT a validated finance engine (that is T3.1, gated by HC-5).

Run (no project dependencies needed):
  uv run --with reportlab==4.2.5 --with pyyaml==6.0.2 python eval/tools/gen_agreements.py
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

DATASET_VERSION = "agreements-v1"
GST = Decimal("0.18")  # synthetic assumption, same label as doc 06 (HC-5 pending)
OUT = Path(__file__).resolve().parents[1] / "datasets" / "agreements"

ALL_TRAPS = {
    "fee_pct_plus_gst": '"2% + GST" processing fee',
    "fee_in_footnote": "a fee that appears only in a footnote",
    "insurance_deducted": "insurance premium deducted from the disbursement",
    "flat_rate_small_print": "flat rate disclosed only in small print",
    "prepayment_on_floating": "prepayment charge on a floating-rate loan",
    "penal_interest": "penal-interest clause",
}


def q(x: Decimal, places: str = "1") -> Decimal:
    return x.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def inr(x: Decimal) -> str:
    """Indian digit grouping, e.g. 12,50,000."""
    s = str(q(x))
    neg, s = s.startswith("-"), s.lstrip("-")
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    out = ",".join([*groups, tail]) if groups else tail
    return ("-" if neg else "") + "Rs. " + out


@dataclass
class Spec:
    id: str
    lender: str
    product: str
    borrower: str
    principal: Decimal
    rate: Decimal                 # annual %, as printed
    basis: str                    # reducing | flat
    rate_type: str                # fixed | floating
    tenure: int                   # months
    fee_pct: Decimal | None = None
    fee_flat: Decimal | None = None
    fee_gst_extra: bool = True    # True: "+ GST"; False: inclusive of GST
    insurance: Decimal | None = None
    insurance_deducted: bool = False
    prepay_pct: Decimal | None = None
    penal_pct_pm: Decimal | None = None
    bounce: Decimal | None = None
    doc_fee_footnote: Decimal | None = None
    traps: list[str] = field(default_factory=list)
    benchmark: str | None = None  # floating-rate benchmark text


SPECS = [
    Spec("AG01", "Suryoday Finserv Pvt. Ltd. (fictional)", "Personal Loan", "Asha Verma (fictional)",
         Decimal(300000), Decimal("14.50"), "reducing", "fixed", 36, fee_pct=Decimal(2), bounce=Decimal(500),
         traps=["fee_pct_plus_gst"]),
    Spec("AG02", "Navkiran Credit Ltd. (fictional)", "Personal Loan", "Rohit Sharma (fictional)",
         Decimal(500000), Decimal("13.25"), "reducing", "fixed", 48, fee_flat=Decimal(4999), fee_gst_extra=False,
         doc_fee_footnote=Decimal(1500), traps=["fee_in_footnote"]),
    Spec("AG03", "Prakash Lending Co. (fictional)", "Personal Loan", "Meena Iyer (fictional)",
         Decimal(400000), Decimal("15.00"), "reducing", "fixed", 36, fee_pct=Decimal("1.5"),
         insurance=Decimal(8400), insurance_deducted=True, traps=["fee_pct_plus_gst", "insurance_deducted"]),
    Spec("AG04", "Gharsaaz Consumer Finance (fictional)", "Consumer Durable Loan", "Imran Khan (fictional)",
         Decimal(60000), Decimal("11.00"), "flat", "fixed", 12, fee_flat=Decimal(999), fee_gst_extra=False,
         traps=["flat_rate_small_print"]),
    Spec("AG05", "Aangan Home Finance Ltd. (fictional)", "Home Loan", "Priya & Karan Mehta (fictional)",
         Decimal(2500000), Decimal("8.75"), "reducing", "floating", 240, fee_pct=Decimal("0.5"),
         prepay_pct=Decimal(2), benchmark="the Lender's External Benchmark Lending Rate (EBLR) + 2.25%",
         traps=["fee_pct_plus_gst", "prepayment_on_floating"]),
    Spec("AG06", "Raftaar Two-Wheeler Finance (fictional)", "Two-Wheeler Loan", "Sunil Yadav (fictional)",
         Decimal(85000), Decimal("16.00"), "reducing", "fixed", 24, fee_flat=Decimal(1500),
         penal_pct_pm=Decimal(2), bounce=Decimal(590), traps=["penal_interest"]),
    Spec("AG07", "Udyam Capital Partners (fictional)", "Business Loan", "Kavya Traders, prop. Kavya Nair (fictional)",
         Decimal(1000000), Decimal("17.50"), "reducing", "fixed", 36, fee_pct=Decimal(2),
         insurance=Decimal(18500), insurance_deducted=True, penal_pct_pm=Decimal(3),
         traps=["fee_pct_plus_gst", "insurance_deducted", "penal_interest"]),
    Spec("AG08", "Swarnim Gold & Personal Loans (fictional)", "Personal Loan", "Deepak Joshi (fictional)",
         Decimal(150000), Decimal("12.00"), "flat", "fixed", 24, fee_pct=Decimal("2.5"),
         doc_fee_footnote=Decimal(750), traps=["flat_rate_small_print", "fee_in_footnote", "fee_pct_plus_gst"]),
    Spec("AG09", "Sthir Property Finance Ltd. (fictional)", "Loan Against Property", "Neha Kulkarni (fictional)",
         Decimal(1800000), Decimal("10.40"), "reducing", "floating", 120, fee_pct=Decimal(1),
         prepay_pct=Decimal(4), penal_pct_pm=Decimal(2), benchmark="the Lender's Prime Lending Rate (PLR) - 4.10%",
         traps=["fee_pct_plus_gst", "prepayment_on_floating", "penal_interest"]),
    Spec("AG10", "Sahaj Finance Ltd. (fictional)", "Personal Loan", "Farah Siddiqui (fictional)",
         Decimal(200000), Decimal("12.75"), "reducing", "fixed", 24, fee_flat=Decimal(2360), fee_gst_extra=False,
         traps=[]),  # control: no traps
]


def emi(s: Spec) -> Decimal:
    p, n = s.principal, s.tenure
    if s.basis == "flat":
        return q((p + p * s.rate / 100 * n / 12) / n)
    r = s.rate / 100 / 12
    f = (1 + r) ** n
    return q(p * r * f / (f - 1))


def fee_amounts(s: Spec) -> tuple[Decimal, Decimal]:
    """(fee before GST, GST on fee)."""
    base = s.principal * s.fee_pct / 100 if s.fee_pct is not None else (s.fee_flat or Decimal(0))
    if s.fee_gst_extra:
        return q(base), q(base * GST)
    pre = q(base / (1 + GST))
    return pre, q(base) - pre


def build(s: Spec) -> None:
    e = emi(s)
    fee, fee_gst = fee_amounts(s)
    deductions = fee + fee_gst + (s.insurance if s.insurance_deducted and s.insurance else 0) \
        + (s.doc_fee_footnote or 0)
    net = s.principal - deductions
    total_repay = e * s.tenure

    terms: list[dict] = []

    def term(field_: str, quantity: str, value: Decimal | bool, raw: str, page: int, where: str, **kw) -> str:
        t = {"field": field_, "quantity": quantity,
             "value": value if isinstance(value, bool) else str(value), "raw_text": raw,
             "page": page, "location": where}
        t.update({k: v for k, v in kw.items() if v is not None})
        terms.append(t)
        return raw

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=12.5)
    small = ParagraphStyle("s", parent=body, fontSize=5.5, leading=6.5, textColor=colors.HexColor("#444444"))
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=15)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5)

    flow: list = [Paragraph(s.lender, h1), Paragraph(f"Key Fact Statement &amp; Loan Agreement — {s.product}", h2),
                  Paragraph(f"Agreement No. SYN-{s.id}-2026 &nbsp;&nbsp; Borrower: {s.borrower} &nbsp;&nbsp; "
                            "SYNTHETIC TEST DOCUMENT — NOT A REAL OFFER", body), Spacer(1, 4 * mm)]

    # --- Page 1: KFS table ---------------------------------------------------------------------------
    flat_hidden = "flat_rate_small_print" in s.traps
    rate_cell = f"{s.rate}% p.a." if flat_hidden else f"{s.rate}% p.a. ({s.basis}, {s.rate_type})"
    rate_extra = {"rate_basis": s.basis, "rate_type": s.rate_type, "period": "annual"}
    rows = [["Item", "Details"],
            ["Loan amount", term("loan_amount", "amount_inr", s.principal, inr(s.principal), 1, "kfs_table")],
            ["Rate of interest", term("interest_rate", "percent", s.rate, rate_cell, 1, "kfs_table",
                                      basis_disclosed_in="small_print" if flat_hidden else "kfs_table", **rate_extra)],
            ["Tenure", term("tenure", "months", Decimal(s.tenure), f"{s.tenure} months", 1, "kfs_table")],
            ["EMI", term("emi", "amount_inr", e, f"{inr(e)} per month", 1, "kfs_table", period="monthly")]]
    if s.fee_pct is not None or s.fee_flat is not None:
        if s.fee_pct is not None:
            raw = f"{s.fee_pct}% of loan amount + GST" if s.fee_gst_extra else f"{s.fee_pct}% (incl. GST)"
            term("processing_fee", "percent", s.fee_pct, raw, 1, "kfs_table", of_what="loan_amount",
                 includes_gst=not s.fee_gst_extra, period="one_time")
        else:
            raw = f"{inr(s.fee_flat)} (inclusive of GST)" if not s.fee_gst_extra else f"{inr(s.fee_flat)} + GST"
            term("processing_fee", "amount_inr", s.fee_flat, raw, 1, "kfs_table",
                 includes_gst=not s.fee_gst_extra, period="one_time")
        rows.append(["Processing fee", raw + ("¹" if s.doc_fee_footnote else "")])
    if s.insurance:
        raw = (f"{inr(s.insurance)} (credit-shield premium, deducted from disbursement)" if s.insurance_deducted
               else f"{inr(s.insurance)} (optional)")
        rows.append(["Insurance", term("insurance_bundled", "amount_inr", s.insurance, raw, 1, "kfs_table",
                                       deducted_from_disbursement=s.insurance_deducted, period="one_time")])
    rows.append(["Net disbursed amount", term("net_disbursement", "amount_inr", net, inr(net), 1, "kfs_table")])
    rows.append(["Total amount payable", term("total_cost", "amount_inr", total_repay,
                                              f"{inr(total_repay)} over the tenure", 1, "kfs_table")])
    if s.bounce:
        rows.append(["Cheque/ECS bounce", term("other_charge", "amount_inr", s.bounce,
                                               f"{inr(s.bounce)} per instance", 1, "kfs_table",
                                               of_what="bounce_charge", period="per_instance")])

    t = Table(rows, colWidths=[55 * mm, 110 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                           ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow += [t, Spacer(1, 4 * mm)]

    if flat_hidden:
        flow.append(Paragraph(term(
            "interest_rate", "percent", s.rate,
            f"* Interest is computed on a flat basis on the original loan amount for the full tenure; "
            f"the rate of {s.rate}% p.a. stated above is a flat rate.",
            1, "small_print", rate_basis="flat", rate_type=s.rate_type, period="annual"), small))
    if s.doc_fee_footnote:
        flow.append(Paragraph(term(
            "other_charge", "amount_inr", s.doc_fee_footnote,
            f"¹ A documentation and stamping charge of {inr(s.doc_fee_footnote)} is also payable and will be "
            "deducted from the disbursement.", 1, "footnote", of_what="documentation_charge",
            deducted_from_disbursement=True, period="one_time"), small))
    flow.append(PageBreak())

    # --- Page 2: clauses ------------------------------------------------------------------------------
    flow.append(Paragraph("Terms and Conditions", h2))
    n = 1

    def clause(title: str, text: str) -> None:
        nonlocal n
        flow.append(Paragraph(f"<b>{n}. {title}.</b> {text}", body))
        flow.append(Spacer(1, 2.5 * mm))
        n += 1

    clause("Loan", f"The Lender agrees to lend and the Borrower agrees to borrow {inr(s.principal)} on the terms "
           "set out in the Key Fact Statement, which forms part of this Agreement.")
    if s.rate_type == "floating":
        clause("Interest", term("interest_rate", "percent", s.rate,
                                f"Interest is payable at a floating rate linked to {s.benchmark}, currently "
                                f"{s.rate}% p.a. on a reducing-balance basis, reset quarterly.",
                                2, "clause", rate_basis="reducing", rate_type="floating", period="annual"))
    else:
        clause("Interest", f"Interest is payable monthly in arrears as set out in the Key Fact Statement.")
    clause("Repayment", f"The Borrower shall repay the loan in {s.tenure} equated monthly instalments by "
           "NACH mandate on the 5th of each month.")
    if s.fee_pct is not None and s.fee_gst_extra:
        clause("Processing fee", term("processing_fee", "percent", s.fee_pct,
                                      f"A non-refundable processing fee of {s.fee_pct}% of the loan amount plus "
                                      "applicable GST will be deducted from the disbursement.", 2, "clause",
                                      of_what="loan_amount", includes_gst=False, period="one_time"))
    if s.insurance_deducted and s.insurance:
        clause("Insurance", term("insurance_bundled", "amount_inr", s.insurance,
                                 f"The Borrower is enrolled in a group credit-shield cover; the single premium of "
                                 f"{inr(s.insurance)} is deducted from the loan amount at disbursement.", 2,
                                 "clause", deducted_from_disbursement=True, period="one_time"))
    if s.prepay_pct is not None:
        clause("Prepayment", term("prepayment_charge", "percent", s.prepay_pct,
                                  f"Part or full prepayment is permitted after 6 EMIs and attracts a prepayment "
                                  f"charge of {s.prepay_pct}% of the principal outstanding plus GST.", 2, "clause",
                                  of_what="principal_outstanding", includes_gst=False, period="one_time",
                                  rate_type="floating" if s.rate_type == "floating" else None))
    else:
        clause("Prepayment", "The Borrower may prepay the loan in full at any time without any charge.")
        term("prepayment_charge", "percent", Decimal(0), "without any charge", 2, "clause",
             of_what="principal_outstanding")
    if s.penal_pct_pm is not None:
        clause("Default", term("penal_charge", "percent", s.penal_pct_pm,
                               f"On delay in payment of any EMI, penal interest at {s.penal_pct_pm}% per month "
                               "shall be levied on the overdue amount from the due date until payment.", 2,
                               "clause", of_what="overdue_amount", period="monthly"))
    else:
        clause("Default", "On delay, a late-payment charge as per the Lender's schedule of charges may apply.")
    clause("Governing law", "This Agreement is governed by the laws of India. Courts at Mumbai shall have "
           "jurisdiction.")
    flow.append(Spacer(1, 10 * mm))
    flow.append(Paragraph("Signed: ____________________ (Borrower) &nbsp;&nbsp;&nbsp; "
                          "____________________ (for the Lender)", body))

    d = OUT / s.id
    d.mkdir(parents=True, exist_ok=True)
    pdf = d / "agreement.pdf"
    SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm, title=f"SYN-{s.id}", author="SaidSigned eval",
                      creator="eval/tools/gen_agreements.py", invariant=1).build(flow)

    truth = {
        "dataset_version": DATASET_VERSION,
        "id": s.id, "synthetic": True, "lender": s.lender, "borrower": s.borrower, "product": s.product,
        "pages": 2, "language": "en",
        "traps": [{"id": t, "description": ALL_TRAPS[t]} for t in s.traps],
        "derived_by_generator": {
            "note": "Generator arithmetic for the printed figures; not a validated finance engine (HC-5 pending).",
            "fee_before_gst": str(fee), "gst_on_fee": str(fee_gst), "gst_rate_assumed": str(GST),
            "total_deductions": str(q(deductions)),
        },
        "terms": terms,
        "files": {"agreement.pdf": {"sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()},
                  "scan_print.pdf": "TODO (manual: print and scan)",
                  "photo_bright.jpg": "TODO (manual)", "photo_dim.jpg": "TODO (manual)",
                  "photo_mixed.jpg": "TODO (manual)"},
    }
    (d / "terms.yaml").write_text(yaml.safe_dump(truth, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> None:
    for s in SPECS:
        build(s)
    covered = {t for s in SPECS for t in s.traps}
    missing = set(ALL_TRAPS) - covered
    assert not missing, f"traps not covered: {missing}"
    print(f"wrote {len(SPECS)} agreements to {OUT}; all {len(ALL_TRAPS)} trap types covered")


if __name__ == "__main__":
    main()
