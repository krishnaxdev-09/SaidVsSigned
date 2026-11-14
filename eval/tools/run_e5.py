"""E5 (doc 05 §9): OCR on PDF, scan and photos — per-field numeric accuracy, confidence availability, time per page.

Runs the real T2.2 pipeline (photo pre-processing + Docling + RapidOCR) on SIMULATED variants of synthetic agreements:
digital PDF; scan (image-only, grey, 0.7° rotation, noise); phone photos in good / dim / uneven light with perspective
distortion. Real print-and-scan and phone photos (T0.13 manual variants) are still pending.

Run from backend/:  PYTHONPATH=. uv run python ../eval/tools/run_e5.py
"""

from __future__ import annotations

import datetime as dt
import statistics
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image

from app.modules.claims.normalize import _phrases, eval_phrase, tokens
from app.modules.documents import parse

ROOT = Path(__file__).resolve().parents[1]
AGREEMENTS = ["AG01", "AG03", "AG05", "AG07", "AG09"]  # cover all six traps
VARIANTS = ["digital", "scan", "photo_good", "photo_dim", "photo_shadow"]
T_OCR_NUM = 0.80


def numbers(text: str) -> set[Decimal]:
    words = " ".join(tokens(text)).split()
    out = set()
    for a, b in _phrases(words, False):
        r = eval_phrase(words[a:b], False)
        if r is not None:
            out.add(r[0])
    return out


def photo(page: np.ndarray, light: str, rng: np.random.Generator) -> Image.Image:
    h, w = page.shape[:2]
    canvas = np.full((h + 300, w + 300, 3), 70, np.uint8)
    j = rng.integers(-40, 40, size=(4, 2))
    dst = np.float32([[150, 120], [w + 140, 150], [w + 160, h + 200], [130, h + 170]]) + j.astype(np.float32)
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), dst)
    img = cv2.warpPerspective(page, m, (w + 300, h + 300), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT).astype(np.float32)
    if light == "dim":
        img = img * 0.5 + rng.normal(0, 6, img.shape)
    elif light == "shadow":
        grad = np.linspace(1.0, 0.45, img.shape[1])[None, :, None]
        img = img * grad + rng.normal(0, 4, img.shape)
    else:
        img = img * 0.95 + rng.normal(0, 3, img.shape)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)
    return Image.fromarray(img)


def scan(page: np.ndarray, rng: np.random.Generator) -> Image.Image:
    g = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)  # ~150 DPI
    h, w = g.shape
    g = cv2.warpAffine(g, cv2.getRotationMatrix2D((w / 2, h / 2), 0.7, 1.0), (w, h), borderValue=255)
    g = np.clip(g.astype(np.float32) + rng.normal(0, 8, g.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(g).convert("RGB")


def main() -> None:
    parser = parse.DoclingParser()
    rng = np.random.default_rng(7)
    rows = []
    for ag in AGREEMENTS:
        pdf = ROOT / "datasets" / "agreements" / ag / "agreement.pdf"
        truth = yaml.safe_load((pdf.parent / "terms.yaml").read_text())
        key = [t for t in truth["terms"] if t["location"] == "kfs_table" and t["quantity"] in ("amount_inr", "percent", "months")]
        pages = [np.array(im) for im, _ in parse.render_pdf(pdf)]
        ref_numbers: dict[int, set[Decimal]] = {}
        for variant in VARIANTS:
            with tempfile.TemporaryDirectory() as tmp:
                t0 = time.time()
                if variant == "digital":
                    path, ocr_pages, force = pdf, set(), False
                else:
                    imgs = [scan(p, rng) if variant == "scan" else parse.preprocess_photo(photo(p, variant.split("_")[1], rng)).image
                            for p in pages]
                    path = Path(tmp) / "v.pdf"
                    parse.images_to_pdf(imgs, path)
                    ocr_pages, force = set(range(1, len(pages) + 1)), True
                d = parser.parse(path, ocr=bool(ocr_pages), force_full_page_ocr=force)
                parse.confidence_pass(d, ocr_pages)
                secs = (time.time() - t0) / len(pages)
            page_nums: dict[int, set[Decimal]] = {}
            for it in d.items:
                page_nums.setdefault(it.page, set()).update(numbers(it.text))
            found = sum(1 for t in key if Decimal(str(t["value"])) in page_nums.get(t["page"], set()))
            if variant == "digital":
                ref_numbers = page_nums
            ocr_items = [i for i in d.items if i.text_source == "ocr"]
            confident_wrong = 0
            for it in ocr_items:
                extra = numbers(it.text) - ref_numbers.get(it.page, set())
                if extra and it.ocr_min_numeric_conf is not None and it.ocr_min_numeric_conf >= T_OCR_NUM:
                    confident_wrong += 1
            rows.append({"ag": ag, "variant": variant, "key": len(key), "found": found, "secs": secs,
                         "ocr_items": len(ocr_items), "with_conf": sum(1 for i in ocr_items if i.ocr_conf is not None),
                         "min_num_conf": [i.ocr_min_numeric_conf for i in ocr_items if i.ocr_min_numeric_conf is not None],
                         "confident_wrong": confident_wrong, "ocr_score": d.scores.get("ocr_score")})
            print(f"{ag} {variant:13s} key {found}/{len(key)}  {secs:5.1f}s/page  conf {rows[-1]['with_conf']}/{len(ocr_items)}"
                  f"  confident misreads {confident_wrong}", flush=True)
    L = [f"# E5 — OCR on PDF, scan and photos ({dt.date.today().isoformat()})", "",
         "> **Simulated scans and photos.** Variants are generated from the synthetic agreements (rotation, noise, "
         "perspective, lighting). Real print-and-scan copies and phone photos (T0.13 manual variants) are still pending, "
         "so these figures are optimistic and are not the HC-4 acceptance measurement.", "",
         f"- Agreements {', '.join(AGREEMENTS)} × {', '.join(VARIANTS)}; Docling (layout + TableFormer ACCURATE) with RapidOCR "
         "(torch backend) on OCR pages; photo pre-processing (perspective, deskew, CLAHE); confidence pass from Docling's "
         "OCR text-line confidences (no Tesseract needed).",
         f"- Key numeric fields = KFS-table amounts, percentages and tenures from `terms.yaml`; a field counts as correct when "
         f"its exact value is read on its page. A *confident misread* is an OCR item containing a number not present in the "
         f"digital reading of the same page with `ocr_min_numeric_conf` ≥ T_ocr_num {T_OCR_NUM}.", "",
         "| Variant | Key numeric fields | Time / page | OCR items with confidence | Median min-numeric conf | Confident misreads |",
         "|---|---|---|---|---|---|"]
    for v in VARIANTS:
        R = [r for r in rows if r["variant"] == v]
        k, f = sum(r["key"] for r in R), sum(r["found"] for r in R)
        oi, wc = sum(r["ocr_items"] for r in R), sum(r["with_conf"] for r in R)
        confs = [c for r in R for c in r["min_num_conf"]]
        L.append(f"| {v} | {f}/{k} ({100 * f / k:.1f}%) | {statistics.mean(r['secs'] for r in R):.1f} s | "
                 f"{f'{wc}/{oi}' if oi else 'n/a (text layer)'} | {f'{statistics.median(confs):.3f}' if confs else '—'} | "
                 f"{sum(r['confident_wrong'] for r in R)} |")
    L += ["", "| Agreement | Variant | Key fields | s/page | Confident misreads |", "|---|---|---|---|---|"]
    L += [f"| {r['ag']} | {r['variant']} | {r['found']}/{r['key']} | {r['secs']:.1f} | {r['confident_wrong']} |" for r in rows]
    out = ROOT / "reports" / f"{dt.date.today().isoformat()}_E5_ocr.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
