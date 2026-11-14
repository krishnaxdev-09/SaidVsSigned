"""Document parsing — doc 05 §6 steps 2–5 (T2.2): render, photo pre-process, Docling + OCR, confidence pass.

- Render: every page → WebP at 200 DPI (pypdfium2 for PDFs; the pre-processed image for photos).
- Photo pre-process (OpenCV): perspective correction when a page outline is found, deskew, CLAHE contrast;
  Laplacian-variance blur score → `blurry_page` warning ("Retake page N").
- Docling (layout + TableFormer, ACCURATE) with OCR only where a page has < 50 text-layer chars (RapidOCR, torch
  backend). Photos are assembled into an image-only PDF and fully OCR'd.
- Confidence pass: E5 found Docling exposes per-line OCR confidences (parsed-page text-line cells), so those are used
  (doc 05 §6.5; no Tesseract). Text-layer items get 1.0 and `text_layer`. Each item gets `ocr_conf` (mean of the
  overlapping OCR lines) and `ocr_min_numeric_conf` (min over overlapping lines containing digits, % or ₹).
- Every bbox is normalised server-side to 0–1 with a top-left origin.
Items (text blocks and table cells, with table row/column/header), in Docling reading order, are the hand-off to
clause segmentation (`segment.py`, T2.3).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from PIL import Image

RENDER_DPI = 200
TEXT_LAYER_MIN_CHARS = 50  # doc 05 §6: text layer ≥ 50 chars/page → OCR off for that page
UNREADABLE_MIN_CHARS = 50  # doc 05 §6.5: < 50 OCR chars → page_unreadable
BLUR_MIN_VARIANCE = 100.0  # [A] Laplacian variance below this → blurry_page; to be calibrated with E5 / Phase 4
_NUMERIC = re.compile(r"[0-9०-९%₹]")


# ---- geometry ---------------------------------------------------------------------------------------------------------

def normalise_bbox(left: float, t: float, r: float, b: float, page_w: float, page_h: float,
                   origin: Literal["TOPLEFT", "BOTTOMLEFT"]) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) in 0..1 with a top-left origin. Docling uses BOTTOMLEFT for PDF items (t > b) and TOPLEFT for
    OCR cells; both are accepted. Values are clamped and ordered."""
    if page_w <= 0 or page_h <= 0:
        raise ValueError("page size must be positive")
    if origin == "BOTTOMLEFT":
        y_top, y_bottom = page_h - max(t, b), page_h - min(t, b)
    else:
        y_top, y_bottom = min(t, b), max(t, b)
    x0, x1 = sorted((left, r))

    def c(v: float) -> float:
        return min(1.0, max(0.0, v))
    return c(x0 / page_w), c(y_top / page_h), c(x1 / page_w), c(y_bottom / page_h)


def overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Intersection area divided by the area of `b` (how much of b lies inside a)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area_b = max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))
    return max(0.0, w) * max(0.0, h) / area_b


# ---- photo pre-processing --------------------------------------------------------------------------------------------

@dataclass
class Preprocessed:
    image: Image.Image
    blur: float
    perspective_fixed: bool
    deskew_deg: float


def preprocess_photo(img: Image.Image) -> Preprocessed:
    import cv2

    rgb = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    h, w = gray.shape
    fixed = False
    # Perspective: the largest 4-corner contour covering ≥ 30% of the frame is taken as the page outline.
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
        if len(approx) == 4 and cv2.contourArea(approx) >= 0.3 * w * h:
            pts = approx.reshape(4, 2).astype("float32")
            s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
            src = np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], dtype="float32")
            ww = int(max(np.linalg.norm(src[0] - src[1]), np.linalg.norm(src[3] - src[2])))
            hh = int(max(np.linalg.norm(src[0] - src[3]), np.linalg.norm(src[1] - src[2])))
            if ww > 100 and hh > 100:
                m = cv2.getPerspectiveTransform(src, np.array([[0, 0], [ww, 0], [ww, hh], [0, hh]], dtype="float32"))
                rgb = cv2.warpPerspective(rgb, m, (ww, hh), borderValue=(255, 255, 255))
                gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
                fixed = True
            break
    # Deskew: angle of the minimum-area rectangle around dark (ink) pixels; small angles only.
    ink = np.column_stack(np.where(gray < 128))
    angle = 0.0
    if len(ink) > 500:
        a = cv2.minAreaRect(ink[:, ::-1].astype("float32"))[2]
        a = a - 90 if a > 45 else a
        if 0.3 <= abs(a) <= 15:
            angle = float(a)
            hh, ww = gray.shape
            m = cv2.getRotationMatrix2D((ww / 2, hh / 2), angle, 1.0)
            rgb = cv2.warpAffine(rgb, m, (ww, hh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    # CLAHE on the lightness channel.
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return Preprocessed(Image.fromarray(out), blur, fixed, angle)


# ---- rendering ---------------------------------------------------------------------------------------------------------

def render_pdf(pdf_path: Path) -> list[tuple[Image.Image, int]]:
    """[(page image at 200 DPI, text-layer character count)]."""
    import pypdfium2 as pdfium

    out = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in pdf:
            chars = len(page.get_textpage().get_text_range().strip())
            out.append((page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB"), chars))
    finally:
        pdf.close()
    return out


def images_to_pdf(images: list[Image.Image], dst: Path) -> None:
    """Image-only PDF (one page per photo) so photos go through the same Docling PDF pipeline, fully OCR'd."""
    first, *rest = [im.convert("RGB") for im in images]
    first.save(dst, format="PDF", resolution=RENDER_DPI, save_all=True, append_images=rest)


# ---- parser output -----------------------------------------------------------------------------------------------------

@dataclass
class Item:
    ref: str
    page: int
    label: str
    text: str
    bbox: tuple[float, float, float, float] | None
    table: dict[str, int | str | bool] | None = None  # {"table": ref, "row": i, "col": j, "header": bool}
    text_source: str = "text_layer"
    ocr_conf: float | None = None
    ocr_min_numeric_conf: float | None = None


@dataclass
class OcrLine:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass
class ParsedDoc:
    items: list[Item]
    ocr_lines: list[OcrLine]
    docling_json: dict[str, Any]
    scores: dict[str, float | None] = field(default_factory=dict)


class DocumentParser(Protocol):
    name: str

    def parse(self, pdf_path: Path, *, ocr: bool, force_full_page_ocr: bool) -> ParsedDoc: ...


class DoclingParser:
    """Docling with layout + TableFormer (ACCURATE) and RapidOCR on the torch backend. The converter is built once
    per process (model loading is slow)."""

    name = "docling"

    def __init__(self) -> None:
        self._converters: dict[tuple[bool, bool], Any] = {}

    def _converter(self, ocr: bool, force: bool) -> Any:
        key = (ocr, force)
        if key not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                OcrMode,
                PdfPipelineOptions,
                RapidOcrOptions,
                TableFormerMode,
                TableStructureOptions,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption

            ocr_opts = RapidOcrOptions(mode=OcrMode.FULL_PAGE if force else OcrMode.DEFAULT, backend="torch")
            o = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True, generate_parsed_pages=True, ocr_options=ocr_opts,
                                   table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE))
            self._converters[key] = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=o)})
        return self._converters[key]

    def parse(self, pdf_path: Path, *, ocr: bool, force_full_page_ocr: bool) -> ParsedDoc:
        res = self._converter(ocr, force_full_page_ocr).convert(str(pdf_path))
        doc = res.document
        sizes = {n: (p.size.width, p.size.height) for n, p in doc.pages.items()}

        def nb(prov: Any) -> tuple[float, float, float, float] | None:
            if prov is None or prov.page_no not in sizes:
                return None
            b = prov.bbox
            return normalise_bbox(b.l, b.t, b.r, b.b, *sizes[prov.page_no], b.coord_origin.value)

        items: list[Item] = []
        for node, _level in doc.iterate_items():  # body in Docling reading order (furniture excluded)
            prov = node.prov[0] if getattr(node, "prov", None) else None
            if prov is None or prov.page_no not in sizes:
                continue
            data = getattr(node, "data", None)
            if data is not None and hasattr(data, "table_cells"):  # TableItem
                w, h = sizes[prov.page_no]
                for c in data.table_cells:
                    if not c.text.strip():
                        continue
                    bb = (normalise_bbox(c.bbox.l, c.bbox.t, c.bbox.r, c.bbox.b, w, h, c.bbox.coord_origin.value)
                          if c.bbox is not None else nb(prov))
                    items.append(Item(ref=f"{node.self_ref}/r{c.start_row_offset_idx}c{c.start_col_offset_idx}",
                                      page=prov.page_no, label="table_cell", text=c.text, bbox=bb,
                                      table={"table": node.self_ref, "row": c.start_row_offset_idx,
                                             "col": c.start_col_offset_idx, "header": bool(c.column_header)}))
                continue
            txt = getattr(node, "text", "") or ""
            if not txt.strip():
                continue
            marker = (getattr(node, "marker", "") or "").strip()
            if marker and marker not in ("-", "•", "·") and not txt.lstrip().startswith(marker):
                txt = f"{marker} {txt}"  # list numbering ("1.") is split off by Docling; keep the printed text
            items.append(Item(ref=node.self_ref, page=prov.page_no, label=node.label.value, text=txt, bbox=nb(prov)))
        lines: list[OcrLine] = []
        # Result pages are matched to document pages by order (page_no is 0- or 1-based across Docling versions).
        for page_no, p in zip(sorted(sizes), res.pages, strict=False):
            pp = p.parsed_page
            if pp is None:
                continue
            w, h = sizes[page_no]
            for cell in pp.textline_cells or []:
                if getattr(cell, "from_ocr", False):
                    r = cell.rect.to_bounding_box()
                    lines.append(OcrLine(page=page_no, text=cell.text, confidence=float(cell.confidence),
                                         bbox=normalise_bbox(r.l, r.t, r.r, r.b, w, h, r.coord_origin.value)))
        conf = getattr(res, "confidence", None)
        scores = {k: (None if conf is None or (v := getattr(conf, k, None)) is None or v != v else float(v))
                  for k in ("ocr_score", "layout_score", "parse_score", "table_score")}
        return ParsedDoc(items=items, ocr_lines=lines, docling_json=doc.export_to_dict(), scores=scores)


def confidence_pass(parsed: ParsedDoc, ocr_pages: set[int]) -> None:
    """doc 05 §6.5: text-layer items → 1.0; OCR items → mean / min-numeric of overlapping OCR line confidences."""
    by_page: dict[int, list[OcrLine]] = {}
    for line in parsed.ocr_lines:
        by_page.setdefault(line.page, []).append(line)
    for it in parsed.items:
        if it.page not in ocr_pages:
            it.text_source, it.ocr_conf, it.ocr_min_numeric_conf = "text_layer", 1.0, 1.0
            continue
        it.text_source = "ocr"
        hits = [ln for ln in by_page.get(it.page, []) if it.bbox and overlap(it.bbox, ln.bbox) >= 0.5]
        if not hits and it.bbox:  # items larger than one line: lines lying mostly inside the item
            hits = [ln for ln in by_page.get(it.page, []) if overlap(ln.bbox, it.bbox) >= 0.5 or overlap(it.bbox, ln.bbox) >= 0.5]
        if hits:
            it.ocr_conf = sum(ln.confidence for ln in hits) / len(hits)
            numeric = [ln.confidence for ln in hits if _NUMERIC.search(ln.text)]
            it.ocr_min_numeric_conf = min(numeric) if numeric else (it.ocr_conf if not _NUMERIC.search(it.text) else None)
        # no overlapping OCR line → confidences stay None, which the OCR gate treats as low (doc 08 §5)


def items_json(parsed: ParsedDoc) -> list[dict[str, Any]]:
    return [asdict(i) for i in parsed.items]
