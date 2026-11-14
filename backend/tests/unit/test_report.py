"""T3.3 report: snapshot of the rendered content, wording rules (doc 01 §5, doc 08 §8), PDF text round trip."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2 as pdfium

from app.modules.consent.service import attestation_note
from app.modules.report import render
from app.schemas.verification import VerificationResult
from app.schemas.wording import wording_violations

SNAP = Path(__file__).parents[1] / "snapshots" / "report_v1.txt"
U = uuid.UUID


def results() -> list[VerificationResult]:
    spoken = {"kind": "spoken", "claim_id": str(U(int=1)), "quote": "the processing fee is one point five percent.",
              "quote_anchors": [{"segment_id": str(U(int=9)), "transcript_revision": 1, "start_char": 0, "end_char": 10}],
              "start_ms": 25000, "end_ms": 28000, "speaker_role": "agent", "speaker_source": "channel",
              "role_assignment": "user_confirmed", "asr_avg_conf": 0.99, "asr_min_numeric_conf": 0.99,
              "quote_confirmed_by_user": True}
    doc = {"kind": "document", "document_id": str(U(int=5)), "document_version": 1, "page": 1, "printed_page": None,
           "clause_id": str(U(int=6)), "clause_text": "Item | Details\nProcessing fee | 0.5% of loan amount + GST",
           "bboxes": [{"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.24}], "ocr_conf": 1.0, "ocr_min_numeric_conf": 1.0,
           "text_source": "text_layer"}
    base = {"run_id": str(U(int=2)), "version": 2, "is_current": True, "engine_verdicts": [],
            "changed_because": {"trigger": "quote_confirmed", "previous_status": "UNCLEAR", "previous_version": 1},
            "thresholds_version": "t", "created_at": "2026-11-14T12:00:00Z", "primary_engine": "contract"}
    return [VerificationResult.model_validate({**base, "id": str(U(int=10)), "claim_id": str(U(int=1)), "status": "CONFLICT",
            "reason_code": None, "finding_kind": "contractual", "headline": "Possible mismatch: processing fee",
            "reason": 'The agent said "the processing fee is one point five percent." (00:25). Page 1 says: "0.5% + GST".',
            "severity": "medium", "evidence": [spoken, doc],
            "suggested_question": "Ask the agent to explain the difference in writing before you sign."}),
            VerificationResult.model_validate({**base, "id": str(U(int=11)), "claim_id": str(U(int=3)), "status": "NOT_VERIFIED",
            "reason_code": "no_document", "finding_kind": None, "headline": "Couldn't check yet: EMI",
            "reason": "No loan document has been uploaded and processed yet.", "severity": "info",
            "evidence": [{**spoken, "claim_id": str(U(int=3)), "quote": "EMI 22,093", "start_ms": 14000, "role_assignment": "auto"}],
            "suggested_question": "Ask for the KFS / agreement and upload it."})]


def inp(note: str | None) -> render.ReportInput:
    return render.ReportInput(session_id="s-1", lender="Aangan Home Finance (fictional)", product="home_loan",
                              language_hint="hinglish", attestation_note=note, consent_text_version="draft-0.1",
                              generated_at=datetime(2026, 11, 14, 12, 30, tzinfo=UTC), results=results())


def text(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"[{s}] {t}" for s, t in lines) + "\n"


def test_report_snapshot() -> None:
    out = text(render.build(inp(attestation_note("yes"))))
    if not SNAP.exists():  # first run writes the snapshot; review it, then it is fixed
        SNAP.write_text(out, encoding="utf-8")
    assert out == SNAP.read_text(encoding="utf-8")


def test_wording_rules() -> None:
    for answer in ("yes", "no", "unsure"):
        lines = render.build(inp(attestation_note(answer)))  # type: ignore[arg-type]
        body = text(lines)
        assert not wording_violations(body)
        assert "The customer states" in body and "(not independently verified)" in body
        assert "agent consent" not in body.lower() and "agent consented" not in body.lower()
        assert "not legal or financial advice" in body.lower()
    assert "No recording attestation was recorded." in text(render.build(inp(None)))


def test_pdf_contains_the_content() -> None:
    pdf = render.to_pdf(render.build(inp(attestation_note("unsure"))))
    assert pdf.startswith(b"%PDF")
    doc = pdfium.PdfDocument(io.BytesIO(pdf))
    txt = " ".join(" ".join(p.get_textpage().get_text_range().split()) for p in doc)
    for s in ("Possible mismatch: processing fee", "Questions to ask the lender before signing",
              "The customer states the agent may not have been informed", "Hinglish (beta)", "Page 1", "00:25"):
        assert s in txt, s
