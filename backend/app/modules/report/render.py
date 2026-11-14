"""Report content and PDF rendering — doc 10 §Report, doc 01 §5 (T3.3).

`build()` turns a session's current results into a deterministic list of (style, text) lines (snapshot-tested);
`to_pdf()` renders them with reportlab (server-side). Content: summary, every result with its evidence (quote +
time, page + clause excerpt), the questions to ask, the disclaimer, the attestation worded as the customer's own
statement ("The customer states … (not independently verified)" — never agent consent), language support labels and
the generation time. Only user-visible results are included (no shadow rules; finance only after HC-5).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.schemas.verification import STATUS_LABELS, DocumentEvidence, SpokenEvidence, Status, VerificationResult
from app.schemas.wording import mm_ss

Style = Literal["title", "h2", "body", "small", "item", "quote"]
ORDER = [Status.CONFLICT, Status.UNCLEAR, Status.MATCH, Status.NOT_VERIFIED]
LANG_LABEL = {"en": "English (supported)", "hinglish": "Hinglish (beta)", "hi": "Hindi (not yet supported)",
              None: "Not specified"}
DISCLAIMER = ("This report compares what was said on the call with the loan documents you uploaded. It is not legal or "
              "financial advice. A \"Possible mismatch\" is something to clarify with the lender in writing before you "
              "sign; it is not a finding about anyone's intent.")


@dataclass
class ReportInput:
    session_id: str
    lender: str | None
    product: str | None
    language_hint: str | None
    attestation_note: str | None
    consent_text_version: str | None
    generated_at: datetime
    results: list[VerificationResult] = field(default_factory=list)


def _excerpt(text: str, n: int = 260) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def build(r: ReportInput) -> list[tuple[Style, str]]:
    lines: list[tuple[Style, str]] = [("title", "SaidSigned — call vs loan documents"),
                                      ("small", f"Generated {r.generated_at:%d %b %Y, %H:%M} UTC · Session {r.session_id}")]
    if r.lender or r.product:
        lines.append(("body", " · ".join(x for x in (r.lender, (r.product or "").replace("_", " ")) if x)))
    lines += [("small", f"Call language: {LANG_LABEL.get(r.language_hint, r.language_hint or 'Not specified')}"),
              ("small", DISCLAIMER), ("h2", "Summary")]
    results = sorted(r.results, key=lambda x: (ORDER.index(x.status), x.headline))
    lines.append(("body", " · ".join(f"{STATUS_LABELS[s]}: {sum(1 for x in results if x.status == s)}" for s in ORDER)))
    for s in ORDER:
        group = [x for x in results if x.status == s]
        if not group:
            continue
        lines.append(("h2", STATUS_LABELS[s]))
        for x in group:
            lines.append(("item", x.headline + ("" if x.severity == "info" else f" ({x.severity})")))
            lines.append(("body", x.reason))
            spoken = next((e for e in x.evidence if isinstance(e, SpokenEvidence)), None)
            if spoken:
                who = "agent" if spoken.speaker_role == "agent" else spoken.speaker_role
                auto = ", auto-detected" if spoken.role_assignment == "auto" else ""
                lines.append(("quote", f"Said ({who}{auto}) at {mm_ss(spoken.start_ms)}: “{spoken.quote}”"))
            for d in (e for e in x.evidence if isinstance(e, DocumentEvidence)):
                lines.append(("quote", f"Document page {d.page}: “{_excerpt(d.clause_text)}”"))
            if x.suggested_question:
                lines.append(("small", f"Question: {x.suggested_question}"))
    questions = sorted({x.suggested_question for x in results if x.suggested_question and x.status in
                        (Status.CONFLICT, Status.UNCLEAR)})
    if questions:
        lines.append(("h2", "Questions to ask the lender before signing"))
        lines += [("item", q) for q in questions]
    lines.append(("h2", "About this recording"))
    lines.append(("body", r.attestation_note or "No recording attestation was recorded."))
    if r.consent_text_version:
        lines.append(("small", f"Processing consent text version {r.consent_text_version} (draft wording; legal review pending)."))
    return lines


def to_pdf(lines: list[tuple[Style, str]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    styles = {"title": ss["Title"], "h2": ss["Heading2"], "body": ParagraphStyle("b", parent=ss["BodyText"], fontSize=10),
              "small": ParagraphStyle("s", parent=ss["BodyText"], fontSize=8.5, textColor=colors.HexColor("#444444")),
              "item": ParagraphStyle("i", parent=ss["BodyText"], fontSize=10.5, fontName="Helvetica-Bold", spaceBefore=6),
              "quote": ParagraphStyle("q", parent=ss["BodyText"], fontSize=9.5, leftIndent=8 * mm)}

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("₹", "Rs. ")
                .replace("“", '"').replace("”", '"').replace("…", "...").replace("—", "-"))

    flow = []
    for style, t in lines:
        flow.append(Paragraph(esc(t), styles[style]))
        if style == "title":
            flow.append(Spacer(1, 2 * mm))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title="SaidSigned report", author="SaidSigned", invariant=1).build(flow)
    return buf.getvalue()
