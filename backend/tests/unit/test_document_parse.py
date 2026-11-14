"""T2.2 parsing helpers (doc 05 §6): bbox normalisation (Done-when), overlap, confidence pass, photo pre-processing,
text-layer detection. No models are loaded here."""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.modules.documents import parse
from app.modules.documents.parse import Item, OcrLine, ParsedDoc, confidence_pass, normalise_bbox, overlap

AG07 = Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf"


# ---- bbox normalisation (Done-when) -----------------------------------------------------------------------------

def test_bottomleft_pdf_points_to_topleft_unit_box() -> None:
    # Docling item on an A4 page (595.28 x 841.89 pt), BOTTOMLEFT: t=787.29 (top edge), b=776.65
    x0, y0, x1, y1 = normalise_bbox(57.02, 787.29, 178.43, 776.65, 595.28, 841.89, "BOTTOMLEFT")
    assert (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)) == (0.096, 0.065, 0.300, 0.077)
    assert y0 < y1  # top-left origin: y grows downwards


def test_topleft_pixels_and_swapped_or_out_of_range_coordinates() -> None:
    assert normalise_bbox(165.4, 233.9, 330.8, 467.8, 1654, 2339, "TOPLEFT") == pytest.approx((0.1, 0.1, 0.2, 0.2))
    assert normalise_bbox(330.8, 467.8, 165.4, 233.9, 1654, 2339, "TOPLEFT") == pytest.approx((0.1, 0.1, 0.2, 0.2))
    assert normalise_bbox(-10, -5, 2000, 3000, 1654, 2339, "TOPLEFT") == (0.0, 0.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        normalise_bbox(0, 0, 1, 1, 0, 100, "TOPLEFT")


def test_same_box_in_both_origins_agrees() -> None:
    tl = normalise_bbox(100, 200, 300, 260, 600, 800, "TOPLEFT")
    bl = normalise_bbox(100, 800 - 200, 300, 800 - 260, 600, 800, "BOTTOMLEFT")
    assert tl == pytest.approx(bl)


def test_overlap() -> None:
    assert overlap((0, 0, 1, 1), (0.2, 0.2, 0.4, 0.4)) == pytest.approx(1.0)
    assert overlap((0, 0, 0.5, 1), (0.25, 0, 0.75, 1)) == pytest.approx(0.5)
    assert overlap((0, 0, 0.1, 0.1), (0.5, 0.5, 0.6, 0.6)) == 0.0


# ---- confidence pass (doc 05 §6.5) -------------------------------------------------------------------------------

def _doc() -> ParsedDoc:
    items = [Item("a", 1, "text", "Loan Rs. 10,00,000", (0.1, 0.1, 0.5, 0.15)),
             Item("b", 2, "table_cell", "17.50% p.a.", (0.1, 0.2, 0.3, 0.25)),
             Item("c", 2, "text", "Terms", (0.1, 0.3, 0.3, 0.35)),
             Item("d", 2, "text", "Fee 2%", (0.6, 0.6, 0.8, 0.65))]  # no OCR line overlaps it
    lines = [OcrLine(2, "17.50% p.a.", (0.1, 0.2, 0.3, 0.25), 0.80), OcrLine(2, "Terms", (0.1, 0.3, 0.3, 0.35), 0.95)]
    return ParsedDoc(items=items, ocr_lines=lines, docling_json={})


def test_text_layer_items_get_full_confidence() -> None:
    d = _doc()
    confidence_pass(d, ocr_pages={2})
    a = d.items[0]
    assert (a.text_source, a.ocr_conf, a.ocr_min_numeric_conf) == ("text_layer", 1.0, 1.0)


def test_ocr_items_take_overlapping_line_confidences() -> None:
    d = _doc()
    confidence_pass(d, ocr_pages={2})
    b, c, dd = d.items[1], d.items[2], d.items[3]
    assert (b.text_source, b.ocr_conf, b.ocr_min_numeric_conf) == ("ocr", pytest.approx(0.80), pytest.approx(0.80))
    assert c.ocr_conf == pytest.approx(0.95) and c.ocr_min_numeric_conf == pytest.approx(0.95)  # no digits
    assert dd.ocr_conf is None and dd.ocr_min_numeric_conf is None  # unknown → treated as low by the OCR gate


# ---- photo pre-processing ------------------------------------------------------------------------------------------

def _page_image() -> np.ndarray:
    img = np.full((1200, 900, 3), 255, np.uint8)
    for i in range(30):
        cv2.putText(img, f"Line {i} Processing fee 2% EMI Rs. 7,750", (40, 60 + i * 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    return img


def test_perspective_is_corrected_on_a_photographed_page() -> None:
    page = _page_image()
    h, w = page.shape[:2]
    canvas = np.full((h + 300, w + 300, 3), 50, np.uint8)
    dst = np.float32([[160, 110], [w + 110, 160], [w + 150, h + 220], [110, h + 170]])
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), dst)
    photo = cv2.warpPerspective(page, m, (w + 300, h + 300), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT)
    pre = parse.preprocess_photo(Image.fromarray(photo))
    assert pre.perspective_fixed and abs(pre.image.width - w) < 0.15 * w


def test_blur_score_flags_blurry_pages() -> None:
    sharp = Image.fromarray(_page_image())
    assert parse.preprocess_photo(sharp).blur >= parse.BLUR_MIN_VARIANCE
    blurry = sharp.filter(ImageFilter.GaussianBlur(8))
    assert parse.preprocess_photo(blurry).blur < parse.BLUR_MIN_VARIANCE


# ---- rendering / text layer ------------------------------------------------------------------------------------

def test_render_and_text_layer_detection(tmp_path: Path) -> None:
    pages = parse.render_pdf(AG07)
    assert len(pages) == 2 and all(chars >= parse.TEXT_LAYER_MIN_CHARS for _, chars in pages)
    assert pages[0][0].size == (1654, 2339)  # A4 at 200 DPI
    scanned = tmp_path / "scan.pdf"
    parse.images_to_pdf([pages[0][0]], scanned)
    (only,) = parse.render_pdf(scanned)
    assert only[1] == 0  # an image-only page has no text layer → OCR


def test_items_json_round_trip() -> None:
    d = _doc()
    confidence_pass(d, {2})
    out = parse.items_json(d)
    assert out[1]["ocr_conf"] == pytest.approx(0.8) and out[1]["bbox"] == (0.1, 0.2, 0.3, 0.25)


def test_webp_render_is_small() -> None:
    im, _ = parse.render_pdf(AG07)[0]
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=85)
    assert buf.tell() < 500_000
