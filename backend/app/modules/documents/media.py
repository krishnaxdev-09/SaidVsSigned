"""Document sniffing, limits and sanitising (doc 05 §4, §6.1; doc 12 upload security). T2.1.

- Formats by magic bytes only: PDF, JPEG, PNG, WebP.
- PDF: every encrypted PDF → `encrypted_pdf` (doc 05 §4, applied strictly by user decision 2026-11-14), including
  owner-password PDFs that open without a password. Sanitising with pikepdf removes JavaScript (document and page
  actions, named JavaScript, JavaScript links), embedded files, file-attachment annotations and XFA forms.
- Photos: Pillow decodes with a pixel limit (decompression-bomb guard), applies the EXIF orientation, then re-encodes,
  dropping EXIF/GPS and all other metadata.
Failures are input errors (never retried).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from PIL import Image, ImageOps

from app.core.retry import InputError

PDF, JPEG, PNG, WEBP = "pdf", "jpeg", "png", "webp"
CONTENT_TYPES = {PDF: "application/pdf", JPEG: "image/jpeg", PNG: "image/png", WEBP: "image/webp"}
EXT = {PDF: "pdf", JPEG: "jpg", PNG: "png", WEBP: "webp"}
MAX_IMAGE_PIXELS = 60_000_000  # a 60 MP photo is the largest accepted; above that is treated as a bomb


def sniff(head: bytes) -> str | None:
    if head[:5] == b"%PDF-":
        return PDF
    if head[:3] == b"\xff\xd8\xff":
        return JPEG
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    return None


def pdf_page_count(path: Path) -> int:
    """Page count; raises InputError(encrypted_pdf) for any encrypted PDF and (unsupported_media) for broken PDFs."""
    try:
        with pikepdf.open(path) as pdf:
            if pdf.is_encrypted:  # includes owner-password-only PDFs (strict doc 05 §4)
                raise InputError("encrypted PDF", "encrypted_pdf")
            return len(pdf.pages)
    except pikepdf.PasswordError as exc:
        raise InputError("password-protected PDF", "encrypted_pdf") from exc
    except pikepdf.PdfError as exc:
        raise InputError("PDF could not be read", "unsupported_media") from exc


@dataclass
class PdfReport:
    pages: int
    removed: list[str]


def _drop(obj: pikepdf.Object, key: str, removed: list[str], label: str) -> None:
    if key in obj:
        del obj[key]
        removed.append(label)


def sanitise_pdf(src: Path, dst: Path) -> PdfReport:
    removed: list[str] = []
    try:
        with pikepdf.open(src) as pdf:
            if pdf.is_encrypted:  # defence in depth: also refused at upload
                raise InputError("encrypted PDF", "encrypted_pdf")
            root = pdf.Root
            _drop(root, "/OpenAction", removed, "open_action")
            _drop(root, "/AA", removed, "document_actions")
            if "/Names" in root:
                _drop(root.Names, "/JavaScript", removed, "javascript")
                _drop(root.Names, "/EmbeddedFiles", removed, "embedded_files")
            if "/AcroForm" in root:
                _drop(root.AcroForm, "/XFA", removed, "xfa")
            for page in pdf.pages:
                _drop(page.obj, "/AA", removed, "page_actions")
                annots = page.obj.get("/Annots")
                if annots is None:
                    continue
                keep = pikepdf.Array()
                for a in annots:
                    if a.get("/Subtype") == "/FileAttachment":
                        removed.append("file_attachment")
                        continue
                    action = a.get("/A")
                    if action is not None and action.get("/S") in ("/JavaScript", "/Launch"):
                        del a["/A"]
                        removed.append("javascript_link")
                    _drop(a, "/AA", removed, "annotation_actions")
                    keep.append(a)
                page.obj.Annots = keep
            pdf.remove_unreferenced_resources()
            pages = len(pdf.pages)
            pdf.save(dst)
    except pikepdf.PasswordError as exc:
        raise InputError("password-protected PDF", "encrypted_pdf") from exc
    except pikepdf.PdfError as exc:
        raise InputError("PDF could not be sanitised", "unsupported_media") from exc
    return PdfReport(pages=pages, removed=sorted(set(removed)))


@dataclass
class ImageReport:
    width: int
    height: int
    had_exif: bool


def sanitise_image(src: Path, dst: Path, fmt: str) -> ImageReport:
    try:
        with Image.open(src) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise InputError("image too large to decode safely", "too_large")
            had_exif = bool(im.getexif()) or "exif" in im.info
            im.load()
            clean = ImageOps.exif_transpose(im)
            if fmt == JPEG and clean.mode not in ("RGB", "L"):
                clean = clean.convert("RGB")
            out = io.BytesIO()
            params = {"quality": 92} if fmt in (JPEG, WEBP) else {}
            clean.save(out, format={JPEG: "JPEG", PNG: "PNG", WEBP: "WEBP"}[fmt], **params)  # no exif/icc/xmp passed
            dst.write_bytes(out.getvalue())
            return ImageReport(clean.width, clean.height, had_exif)
    except InputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InputError("image too large to decode safely", "too_large") from exc
    except (OSError, SyntaxError, ValueError) as exc:  # Pillow raises these for corrupt or truncated images
        raise InputError("image could not be decoded", "unsupported_media") from exc
