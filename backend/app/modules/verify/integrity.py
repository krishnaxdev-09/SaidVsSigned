"""Evidence integrity validator — doc 08 §4 (T2.5). Runs against the DB before a result is persisted.

1. Every quote anchor resolves to a stored segment of that revision and the anchored text equals the quote (NFC +
   whitespace normalised).
2. `clause_text` equals the stored clause text, and page/bboxes match the clause row.
3. A calculation exists (finance, Phase 3). 4. A rule exists, enabled and reviewed (HC-6; none are enabled).
A failure makes the result NOT_VERIFIED(engine_error) — doc 08 §4 says "UNCLEAR(engine_error)", but engine_error
belongs to NOT_VERIFIED in the HC-2-approved REASON_STATUS map, so the schema-valid status is used.
"""

from __future__ import annotations

import re
import unicodedata

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.verification import (
    CalculationEvidence,
    DocumentEvidence,
    Evidence,
    RuleEvidence,
    SpokenEvidence,
)

log = structlog.get_logger()


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip()


async def check(conn: AsyncConnection, evidence: list[Evidence]) -> list[str]:
    problems: list[str] = []
    for e in evidence:
        if isinstance(e, SpokenEvidence):
            parts = []
            for a in e.quote_anchors:
                seg = (await conn.execute(text(
                    "SELECT text FROM transcript_segments WHERE id = :id AND transcript_revision = :r"),
                    {"id": a.segment_id, "r": a.transcript_revision})).scalar()
                if seg is None or a.end_char > len(seg):
                    problems.append(f"anchor_missing:{a.segment_id}")
                    continue
                parts.append(seg[a.start_char:a.end_char])
            if parts and _n(" ".join(parts)) != _n(e.quote):
                problems.append("quote_mismatch")
        elif isinstance(e, DocumentEvidence):
            row = (await conn.execute(text(
                "SELECT text, page_start, page_end, bboxes FROM clauses WHERE id = :id AND document_id = :d "
                "AND version = :v"), {"id": e.clause_id, "d": e.document_id, "v": e.document_version})).first()
            if row is None:
                problems.append(f"clause_missing:{e.clause_id}")
                continue
            if _n(row.text) != _n(e.clause_text):
                problems.append("clause_text_mismatch")
            if not row.page_start <= e.page <= row.page_end:
                problems.append("clause_page_mismatch")
            stored = {tuple(round(x, 4) for x in b["bbox"]) for b in row.bboxes or []}
            if any((round(b.x0, 4), round(b.y0, 4), round(b.x1, 4), round(b.y1, 4)) not in stored for b in e.bboxes):
                problems.append("clause_bbox_mismatch")
        elif isinstance(e, CalculationEvidence):  # 3. trace exists, is user-visible, and its inputs exist
            row = (await conn.execute(text("SELECT inputs, enabled, formula_id FROM calculations WHERE id = :id"),
                                      {"id": e.calculation_id})).first()
            if row is None or not row.enabled or row.formula_id != e.formula_id:
                problems.append(f"calculation_missing:{e.calculation_id}")
                continue
            for name, inp in (row.inputs or {}).items():
                table = inp.get("ref_table")
                if table == "doc_terms" and not (await conn.execute(text(
                        "SELECT 1 FROM doc_terms WHERE id = CAST(:i AS uuid)"), {"i": inp["ref_id"]})).first():
                    problems.append(f"calculation_input_missing:{name}")
                if table == "claim_values" and not (await conn.execute(text(
                        "SELECT 1 FROM claims WHERE id = CAST(:i AS uuid)"), {"i": inp["ref_id"]})).first():
                    problems.append(f"calculation_input_missing:{name}")
        elif isinstance(e, RuleEvidence):
            problems.append("rule_not_enabled")  # no rule is enabled before HC-6
    if problems:
        log.error("evidence_integrity_failed", problems=problems)
    return problems
