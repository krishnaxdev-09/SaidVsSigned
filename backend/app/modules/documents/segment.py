"""Clause segmentation — doc 05 §6 step 6 (T2.3).

Walks parser items in Docling reading order:
- A new clause starts at a section header, a list item (Docling splits "1." / "(a)" into the list marker), a footnote,
  a paragraph that begins with a numbering pattern, or a signature line.
- **Each table row is its own clause**, with the table's header row prepended (only when the parser flagged header
  cells; a header is never assumed).
- Layout models sometimes merge several numbered clauses into one block: an item is split where a sentence ends and
  a new number or a signature line begins (". 2. Interest"); the pieces keep the block's bbox.
- An item without numbering that follows a clause ending mid-sentence (no . ; : at the end) continues that clause.
- A header followed directly by another clause start does not become a header-only clause; it is kept as the
  `section_path` of the clauses below it.
- Page headers/footers (furniture) are skipped.
Clause confidence: `ocr_conf` = mean of item confidences; `ocr_min_numeric_conf` = min over items containing a digit,
% or ₹, and None when any of those items has none (the OCR gate then treats it as low, doc 08 §5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.documents.parse import Item

_HEADERS = {"section_header", "title"}
_STARTS = {"list_item", "footnote"}
_SKIP = {"page_header", "page_footer"}
NUMBERING = re.compile(
    r"^\s*(?:(?:clause|article|section|schedule)\s+[\dA-Z]+[.:)]?"
    r"|\d+(?:\.\d+)+\.?|\d+[.)]|\(?[a-z]\)|\(?[ivx]{1,4}\)|[*†‡¹²³⁴⁵⁶⁷⁸⁹])(?=\s)", re.IGNORECASE)
SIGNATURE = re.compile(r"^\s*(?:signed|signature|witness(?:es)?|place\s*:|date\s*:)", re.IGNORECASE)
_NUMERIC = re.compile(r"[0-9०-९%₹]")
_INNER_SPLIT = re.compile(r"(?<=[.;:])\s+(?=(?:\d{1,2}(?:\.\d+)*[.)]|\([a-z]\)|\([ivx]{1,4}\))\s+[A-Z]|Signed:|Signature)")
_ENDS_SENTENCE = re.compile(r"[.;:!?)\]]\s*$")


def _split(it: Item) -> list[Item]:
    """Split a merged block at inner clause starts; pieces share the block's bbox and confidences."""
    if it.label in _HEADERS or it.table is not None:
        return [it]
    parts = [p for p in _INNER_SPLIT.split(it.text) if p.strip()]
    if len(parts) == 1:
        return [it]
    return [Item(ref=f"{it.ref}#{k}", page=it.page, label=it.label if k == 0 else "text", text=p, bbox=it.bbox,
                 text_source=it.text_source, ocr_conf=it.ocr_conf, ocr_min_numeric_conf=it.ocr_min_numeric_conf)
            for k, p in enumerate(parts)]


@dataclass
class Clause:
    ord: int
    page_start: int
    page_end: int
    label: str  # section | list_item | footnote | text | table_row | signature
    text: str
    section_path: str | None
    is_table_row: bool = False
    items: list[Item] = field(default_factory=list)
    cells: list[str] = field(default_factory=list)  # table row cells in column order (for the KFS lexicon)
    header_cells: list[str] = field(default_factory=list)
    header_only: bool = False

    @property
    def bboxes(self) -> list[dict[str, Any]]:
        return [{"page": i.page, "bbox": list(i.bbox)} for i in self.items if i.bbox is not None]

    @property
    def text_source(self) -> str:
        return "ocr" if any(i.text_source == "ocr" for i in self.items) else "text_layer"

    @property
    def ocr_conf(self) -> float | None:
        c = [i.ocr_conf for i in self.items if i.ocr_conf is not None]
        return sum(c) / len(c) if c else None

    @property
    def ocr_min_numeric_conf(self) -> float | None:
        numeric = [i for i in self.items if _NUMERIC.search(i.text)]
        if not numeric:
            return self.ocr_conf
        if any(i.ocr_min_numeric_conf is None for i in numeric):
            return None
        return min(i.ocr_min_numeric_conf for i in numeric if i.ocr_min_numeric_conf is not None)


def _table_rows(cells: list[Item], section: str | None) -> list[Clause]:
    rows: dict[int, list[Item]] = {}
    for c in cells:
        assert c.table is not None
        rows.setdefault(int(c.table["row"]), []).append(c)
    header_rows = {r for r, cs in rows.items() if any(c.table and c.table.get("header") for c in cs)}

    def cols(cs: list[Item]) -> list[Item]:
        return sorted(cs, key=lambda c: int(c.table["col"]) if c.table else 0)

    header = [c.text.strip() for r in sorted(header_rows) for c in cols(rows[r])]
    out = []
    for r in sorted(rows):
        if r in header_rows:
            continue
        cs = cols(rows[r])
        line = " | ".join(c.text.strip() for c in cs)
        out.append(Clause(ord=0, page_start=min(c.page for c in cs), page_end=max(c.page for c in cs),
                          label="table_row", text=(" | ".join(header) + "\n" + line) if header else line,
                          section_path=section, is_table_row=True, items=cs, cells=[c.text.strip() for c in cs],
                          header_cells=header))
    return out


def segment(items: list[Item]) -> list[Clause]:
    items = [piece for it in items for piece in _split(it)]
    clauses: list[Clause] = []
    cur: Clause | None = None
    section: str | None = None
    i = 0

    def flush() -> None:
        nonlocal cur
        if cur is not None and not cur.header_only:
            clauses.append(cur)
        cur = None

    while i < len(items):
        it = items[i]
        if it.label in _SKIP:
            i += 1
            continue
        if it.label == "table_cell" and it.table is not None:
            j = i
            tref = it.table["table"]
            while j < len(items) and (tb := items[j].table) is not None and tb["table"] == tref:
                j += 1
            flush()
            clauses += _table_rows(items[i:j], section)
            i = j
            continue
        text = it.text.strip()
        if it.label in _HEADERS:
            flush()
            section = text[:200]
            cur = Clause(ord=0, page_start=it.page, page_end=it.page, label="section", text=text,
                         section_path=section, items=[it], header_only=True)
        elif cur is None or NUMBERING.match(text) or SIGNATURE.match(text) or (
                it.label in _STARTS and (cur.header_only or _ENDS_SENTENCE.search(cur.text))):
            flush()
            label = "signature" if SIGNATURE.match(text) else it.label if it.label in _STARTS else "text"
            cur = Clause(ord=0, page_start=it.page, page_end=it.page, label=label, text=text, section_path=section,
                         items=[it])
        else:  # continuation paragraph (or the body under a header)
            cur.text += "\n" + text
            cur.items.append(it)
            cur.page_end = max(cur.page_end, it.page)
            cur.header_only = False
        i += 1
    flush()
    for n, c in enumerate(clauses):
        c.ord = n
    return clauses
