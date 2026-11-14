"""T2.1 document upload, sanitising, versioning, page replace — security fixtures (doc 05 §4–§6, doc 12 uploads).

Hostile and edge-case files are generated here: PDFs with JavaScript / embedded files / launch links / XFA, password
and owner-only encrypted PDFs, corrupt PDFs and images, a decompression bomb, EXIF GPS photos, a renamed executable."""

from __future__ import annotations

import io
import struct
import uuid
import zlib
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from app.core.config import get_settings
from app.modules.documents.ingest import make_doc_ingest_handler
from app.modules.documents.parse import Item, OcrLine, ParsedDoc
from app.worker.runner import Registry, Worker
from tests.conftest import register_and_login, scalar
from tests.integration.test_transcripts import ready_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ---- fixtures --------------------------------------------------------------------------------------------------------

def pdf_bytes(pages: int = 2, *, hostile: bool = False, encryption: pikepdf.Encryption | None = None) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(595, 842))
    if hostile:
        js = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert('x')")))
        pdf.Root.OpenAction = js
        pdf.Root.AA = pikepdf.Dictionary(WC=js)
        emb = pdf.make_stream(b"MZ evil payload")
        spec = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Filespec, F=pikepdf.String("evil.exe"),
                                                    EF=pikepdf.Dictionary(F=emb)))
        pdf.Root.Names = pikepdf.Dictionary(
            JavaScript=pikepdf.Dictionary(Names=pikepdf.Array([pikepdf.String("a"), js])),
            EmbeddedFiles=pikepdf.Dictionary(Names=pikepdf.Array([pikepdf.String("evil.exe"), spec])))
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(), XFA=pdf.make_stream(b"<xdp/>"))
        page = pdf.pages[0].obj
        page.AA = pikepdf.Dictionary(O=js)
        link = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
                                                    Rect=pikepdf.Array([0, 0, 10, 10]), A=js))
        launch = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
                                                      Rect=pikepdf.Array([0, 20, 10, 30]),
                                                      A=pikepdf.Dictionary(S=pikepdf.Name.Launch, F=pikepdf.String("cmd.exe"))))
        attach = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.FileAttachment,
                                                      Rect=pikepdf.Array([0, 40, 10, 50]), FS=spec))
        page.Annots = pikepdf.Array([link, launch, attach])
    out = io.BytesIO()
    pdf.save(out, encryption=encryption or False)
    return out.getvalue()


def jpeg_with_gps(color: str = "red") -> bytes:
    im = Image.new("RGB", (64, 48), color)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x8825] = {1: "N", 2: (28.0, 36.0, 0.0), 3: "E", 4: (77.0, 12.0, 0.0)}  # GPSInfo
    out = io.BytesIO()
    im.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def png(color: str = "blue") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, format="PNG")
    return out.getvalue()


def png_bomb(w: int = 30_000, h: int = 30_000) -> bytes:
    """A tiny file claiming 900 MP: the header alone must be rejected before decoding."""
    def chunk(t: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b"")


async def upload(client, h, sid, files: list[tuple[str, bytes]], kind: str = "kfs", key: str | None = None):  # type: ignore[no-untyped-def]
    return await client.post(f"/api/v1/sessions/{sid}/documents", headers={**h, "Idempotency-Key": key or uuid.uuid4().hex},
                             data={"kind": kind}, files=[("files", (n, b, "application/octet-stream")) for n, b in files])


class FakeParser:
    """Stands in for Docling in CI (no model downloads): one numeric item per page, with OCR lines on OCR pages."""

    name = "fake-parser"

    def __init__(
        self, ocr_conf: float = 0.95, ocr_text: str = "Processing fee 2% of the loan amount plus GST, deducted at disbursement"
    ) -> None:
        self.ocr_conf, self.ocr_text = ocr_conf, ocr_text
        self.calls: list[dict] = []

    def parse(self, pdf_path, *, ocr, force_full_page_ocr):  # type: ignore[no-untyped-def]
        self.calls.append({"ocr": ocr, "force": force_full_page_ocr})
        n = len(pikepdf.open(pdf_path).pages)
        items = [Item(f"#/texts/{i}", i, "text", f"Page {i}: fee 2%", (0.1, 0.1, 0.6, 0.15)) for i in range(1, n + 1)]
        lines = [OcrLine(i, self.ocr_text, (0.1, 0.1, 0.6, 0.15), self.ocr_conf) for i in range(1, n + 1)] if ocr and self.ocr_text else []
        return ParsedDoc(items=items, ocr_lines=lines, docling_json={"name": "fake"}, scores={"ocr_score": self.ocr_conf})


async def drain(engine, store, parser=None, llm=None) -> None:  # type: ignore[no-untyped-def]
    reg = Registry()
    reg.register("doc.ingest", make_doc_ingest_handler(store, parser or FakeParser(), llm))
    w = Worker(engine, reg, lease_seconds=60)
    while await w.run_once():
        pass


async def doc(client, h, did):  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/v1/documents/{did}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---- PDF sanitising ------------------------------------------------------------------------------------------------

async def test_hostile_pdf_is_sanitised(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await upload(client, h, sid, [("kfs.pdf", pdf_bytes(2, hostile=True))])
    assert r.status_code == 202, r.text
    did = r.json()["document_id"]
    await drain(engine, fake_store)
    d = await doc(client, h, did)
    assert (d["status"], d["source"], d["page_count"]) == ("segmented", "pdf", 2)
    assert {"removed_open_action", "removed_javascript", "removed_embedded_files", "removed_xfa",
            "removed_page_actions", "removed_javascript_link", "removed_file_attachment"} <= set(d["warnings"])
    clean = fake_store.objects[("documents", f"{did}/v1/sanitised/document.pdf")]
    with pikepdf.open(io.BytesIO(clean)) as pdf:
        assert not pdf.is_encrypted and len(pdf.pages) == 2
        assert "/OpenAction" not in pdf.Root and "/AA" not in pdf.Root
        assert "/JavaScript" not in pdf.Root.get("/Names", {}) and "/EmbeddedFiles" not in pdf.Root.get("/Names", {})
        assert "/XFA" not in pdf.Root.AcroForm
        page = pdf.pages[0].obj
        assert "/AA" not in page
        for a in page.get("/Annots", []):
            assert a.get("/Subtype") != "/FileAttachment"
            assert "/A" not in a or a.A.get("/S") not in ("/JavaScript", "/Launch")
    assert b"evil payload" not in clean and b"app.alert" not in clean
    assert ("documents", f"{did}/v1/original/document.pdf") in fake_store.objects  # original kept for evidence


async def test_password_protected_pdf_rejected(client):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await upload(client, h, sid, [("x.pdf", pdf_bytes(1, encryption=pikepdf.Encryption(user="secret", owner="o")))])
    assert r.status_code == 400 and r.json()["code"] == "encrypted_pdf"


async def test_owner_only_encrypted_pdf_is_rejected(client, engine):  # type: ignore[no-untyped-def]
    """User decision 2026-11-14: doc 05 applied strictly — every encrypted PDF is rejected, even one that opens
    without a password (owner password only)."""
    h, sid = await ready_session(client)
    raw = pdf_bytes(1, encryption=pikepdf.Encryption(user="", owner="o"))
    with pikepdf.open(io.BytesIO(raw)) as pdf:
        assert pdf.is_encrypted  # the fixture opens without a password but is encrypted
    r = await upload(client, h, sid, [("x.pdf", raw)])
    assert r.status_code == 400 and r.json()["code"] == "encrypted_pdf"
    assert await scalar(engine, "SELECT count(*) FROM documents WHERE session_id = :s", s=uuid.UUID(sid)) == 0


def test_sanitiser_also_refuses_encrypted_pdfs(tmp_path: Path) -> None:
    from app.core.retry import InputError
    from app.modules.documents.media import sanitise_pdf

    src = tmp_path / "in.pdf"
    src.write_bytes(pdf_bytes(1, encryption=pikepdf.Encryption(user="", owner="o")))
    with pytest.raises(InputError) as e:
        sanitise_pdf(src, tmp_path / "out.pdf")
    assert e.value.code == "encrypted_pdf"


@pytest.mark.parametrize(("name", "data", "code", "status"), [
    ("broken.pdf", b"%PDF-1.7\nthis is not a pdf", "unsupported_media", 415),
    ("renamed.pdf", b"MZ\x90\x00" + b"\x00" * 200, "unsupported_media", 415),
    ("empty.pdf", b"", "unsupported_media", 415),
    ("script.png", b"<script>alert(1)</script>", "unsupported_media", 415),
])
async def test_bad_files_rejected_up_front(client, engine, name, data, code, status):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await upload(client, h, sid, [(name, data)])
    assert r.status_code == status and r.json()["code"] == code
    assert await scalar(engine, "SELECT count(*) FROM documents WHERE session_id = :s", s=uuid.UUID(sid)) == 0


async def test_limits(client, monkeypatch):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await upload(client, h, sid, [("long.pdf", pdf_bytes(61))])
    assert r.status_code == 413 and r.json()["code"] == "too_many_pages"
    r = await upload(client, h, sid, [(f"{i}.png", png(f"#{i:06x}")) for i in range(21)])
    assert r.status_code == 413 and r.json()["code"] == "too_many_pages"
    r = await upload(client, h, sid, [("a.pdf", pdf_bytes(1)), ("b.png", png())])
    assert r.status_code == 422
    monkeypatch.setattr(get_settings(), "MAX_PHOTO_MB", 0)
    r = await upload(client, h, sid, [("big.png", png())])
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    monkeypatch.setattr(get_settings(), "MAX_UPLOAD_MB_DOC", 0)
    r = await upload(client, h, sid, [("big.pdf", pdf_bytes(1))])
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    r = await client.post(f"/api/v1/sessions/{sid}/documents", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                          data={"kind": "insurance_policy"}, files=[("files", ("a.png", png()))])
    assert r.status_code == 422  # insurance_policy is deferred


# ---- photo sanitising ----------------------------------------------------------------------------------------------

async def test_photos_lose_exif_and_gps(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    raw = jpeg_with_gps()
    assert Image.open(io.BytesIO(raw)).getexif().get(0x8825) is not None  # the fixture really carries GPS
    r = await upload(client, h, sid, [("p1.jpg", raw), ("p2.png", png())], kind="loan_agreement")
    did = r.json()["document_id"]
    await drain(engine, fake_store)
    d = await doc(client, h, did)
    assert (d["status"], d["source"], d["page_count"]) == ("segmented", "photos", 2)
    assert "removed_exif" in d["warnings"] and d["pages"][0]["width"] == 64
    clean = fake_store.objects[("documents", f"{did}/v1/sanitised/1.jpg")]
    im = Image.open(io.BytesIO(clean))
    assert not im.getexif() and "exif" not in im.info and b"PhoneMaker" not in clean


@pytest.mark.parametrize(("name", "data", "code"), [
    ("bomb.png", png_bomb(), "too_large"),
    ("corrupt.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 50, "unsupported_media"),
])
async def test_dangerous_or_corrupt_images_fail_without_retry(client, engine, fake_store, name, data, code):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await upload(client, h, sid, [(name, data)])
    assert r.status_code == 202  # format sniffed OK; decoding happens in the worker
    did = r.json()["document_id"]
    await drain(engine, fake_store)
    d = await doc(client, h, did)
    assert (d["status"], d["status_code"]) == ("failed", code)
    attempts = await scalar(engine, "SELECT attempts FROM jobs WHERE dedupe_key = :k", k=f"doc.ingest:{did}:1")
    assert attempts == 1
    assert not any(k[1].startswith(f"{did}/v1/sanitised") for k in fake_store.objects)


# ---- dedupe, idempotency, versioning, page replace --------------------------------------------------------------

async def test_dedupe_and_idempotency(client, engine):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    files = [("a.png", png("red")), ("b.png", png("green"))]
    first = await upload(client, h, sid, files, key="k-doc-1")
    again = await upload(client, h, sid, files, key="k-doc-1")
    assert first.status_code == again.status_code == 202 and first.json() == again.json()
    dup = await upload(client, h, sid, files)
    assert dup.status_code == 200 and dup.json()["duplicate"] and dup.json()["document_id"] == first.json()["document_id"]
    reordered = await upload(client, h, sid, list(reversed(files)))
    assert reordered.status_code == 202  # page order is part of the content hash
    conflict = await upload(client, h, sid, [("c.png", png("white"))], key="k-doc-1")
    assert conflict.status_code == 409 and conflict.json()["code"] == "conflict_idempotency_key"


async def test_page_replace_creates_a_new_version(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("1.png", png("red")), ("2.png", png("green")), ("3.png", png("blue"))])).json()["document_id"]
    await drain(engine, fake_store)
    sha1 = await scalar(engine, "SELECT content_sha256 FROM documents WHERE id = :d", d=uuid.UUID(did))
    r = await client.put(f"/api/v1/documents/{did}/pages/2", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                         files={"file": ("retake.jpg", jpeg_with_gps("yellow"))})
    assert r.status_code == 202 and r.json()["version"] == 2, r.text
    rows = {}
    async with engine.connect() as conn:
        from sqlalchemy import text

        for v, n, k in (await conn.execute(text("SELECT version, page, original_key FROM document_pages WHERE document_id = :d"),
                                           {"d": uuid.UUID(did)})).all():
            rows[(v, n)] = k
    assert rows[(2, 1)] == rows[(1, 1)] and rows[(2, 3)] == rows[(1, 3)]  # other pages carried over
    assert rows[(2, 2)] != rows[(1, 2)] and rows[(2, 2)].endswith("/v2/original/2.jpg")
    assert len(rows) == 6  # version 1 rows kept
    assert await scalar(engine, "SELECT content_sha256 FROM documents WHERE id = :d", d=uuid.UUID(did)) != sha1
    await drain(engine, fake_store)
    d = await doc(client, h, did)
    assert (d["version"], d["status"], d["page_count"]) == (2, "segmented", 3) and "removed_exif" in d["warnings"]
    assert ("documents", f"{did}/v2/sanitised/2.jpg") in fake_store.objects
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'document.page_replace' AND target_id = :t",
                        t=uuid.UUID(did)) == 1
    stale = await client.put(f"/api/v1/documents/{did}/pages/9", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                             files={"file": ("x.png", png())})
    assert stale.status_code == 404
    as_pdf = await client.put(f"/api/v1/documents/{did}/pages/1", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                              files={"file": ("x.pdf", pdf_bytes(1))})
    assert as_pdf.status_code == 415


async def test_pdf_pages_cannot_be_replaced(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("k.pdf", pdf_bytes(2))])).json()["document_id"]
    r = await client.put(f"/api/v1/documents/{did}/pages/1", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                         files={"file": ("p.png", png())})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"


async def test_consent_ownership_and_revocation(client, engine, fake_store):  # type: ignore[no-untyped-def]
    _, h0 = await register_and_login(client)
    no_consent = (await client.post("/api/v1/sessions", headers={**h0, "Idempotency-Key": uuid.uuid4().hex},
                                    json={"mode": "upload", "product_type": "personal_loan", "language_hint": "en"})).json()["id"]
    assert (await upload(client, h0, no_consent, [("a.png", png())])).status_code == 403
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("a.png", png("red"))])).json()["document_id"]
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/documents/{did}", headers=other)).status_code == 404
    assert (await client.get(f"/api/v1/sessions/{sid}/documents", headers=other)).status_code == 404
    assert (await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)).status_code == 200
    d = await doc(client, h, did)
    assert (d["status"], d["status_code"]) == ("failed", "consent_revoked")
    await drain(engine, fake_store)
    assert not any(k[1].startswith(f"{did}/v1/sanitised") for k in fake_store.objects)
    listed = (await client.get(f"/api/v1/sessions/{sid}/documents", headers=h)).json()
    assert [x["document_id"] for x in listed] == [did]


def test_fixture_files_are_what_they_claim(tmp_path: Path) -> None:
    from app.modules.documents.media import PNG, sniff

    assert sniff(png_bomb()) == PNG
    with pytest.raises(Image.DecompressionBombError):  # 900 MP claimed by a tiny file: refused before decoding
        Image.open(io.BytesIO(png_bomb()))



# ---- T2.2: render, OCR, confidence, page images ------------------------------------------------------------------

async def test_parse_outputs_pages_items_and_page_image(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    ag07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()
    did = (await upload(client, h, sid, [("ag07.pdf", ag07)])).json()["document_id"]
    parser = FakeParser()
    await drain(engine, fake_store, parser)
    d = await doc(client, h, did)
    assert d["status"] == "segmented" and d["stage"] == "segmenting"
    assert parser.calls == [{"ocr": False, "force": False}]  # digital PDF: text layer on every page, OCR off
    assert [p["text_source"] for p in d["pages"]] == ["text_layer", "text_layer"] and d["pages"][0]["width"] == 1654
    for name in ("pages/1.webp", "pages/2.webp", "docling.json", "items.json"):
        assert ("documents", f"{did}/v1/{name}") in fake_store.objects
    import json

    items = json.loads(fake_store.objects[("documents", f"{did}/v1/items.json")])
    assert items["parser"] == "fake-parser" and items["items"][0]["text_source"] == "text_layer"
    r = await client.get(f"/api/v1/documents/{did}/pages/1/image", headers=h)
    assert r.status_code == 200 and r.json()["url"].endswith(f"{did}/v1/pages/1.webp")
    assert (await client.get(f"/api/v1/documents/{did}/pages/9/image", headers=h)).status_code == 404


async def test_photos_are_ocred_with_confidences(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("1.png", png("white")), ("2.png", png("gray"))])).json()["document_id"]
    parser = FakeParser(ocr_conf=0.93)
    await drain(engine, fake_store, parser)
    d = await doc(client, h, did)
    assert parser.calls == [{"ocr": True, "force": True}]
    assert [p["text_source"] for p in d["pages"]] == ["ocr", "ocr"] and d["pages"][0]["ocr_conf"] == pytest.approx(0.93)
    import json

    items = json.loads(fake_store.objects[("documents", f"{did}/v1/items.json")])["items"]
    assert all(i["text_source"] == "ocr" and i["ocr_conf"] == pytest.approx(0.93) for i in items)
    assert "blurry_page:1" in d["warnings"]  # a flat colour image has no detail: flagged for a retake


@pytest.mark.parametrize(("parser", "warning"), [
    (FakeParser(ocr_text=""), "page_unreadable:1"),  # no OCR text on the page
    (FakeParser(ocr_conf=0.4), "low_ocr_page:1"),  # page OCR confidence below T_ocr 0.70
])
async def test_ocr_page_warnings(client, engine, fake_store, parser, warning):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("scan.pdf", pdf_bytes(1))])).json()["document_id"]  # blank: no text layer
    await drain(engine, fake_store, parser)
    d = await doc(client, h, did)
    assert d["status"] == "segmented" and warning in d["warnings"] and d["pages"][0]["text_source"] == "ocr"


# ---- T2.3 clause segmentation + doc_terms ---------------------------------------------------------------------------

class KfsParser:
    """Docling-shaped output for a 2-page KFS + agreement (reading order, table with flagged header row)."""

    name = "kfs-parser"

    def parse(self, pdf_path, *, ocr, force_full_page_ocr):  # type: ignore[no-untyped-def]
        def cell(r: int, c: int, t: str, hdr: bool = False) -> Item:
            return Item(f"#/tables/0/r{r}c{c}", 1, "table_cell", t, (0.1 + 0.4 * c, 0.2 + 0.05 * r, 0.5 + 0.4 * c, 0.24 + 0.05 * r),
                        table={"table": "#/tables/0", "row": r, "col": c, "header": hdr})
        items = [Item("#/texts/0", 1, "page_header", "Udyam Capital — page 1", (0.1, 0.01, 0.9, 0.03)),
                 Item("#/texts/1", 1, "section_header", "Key Fact Statement", (0.1, 0.05, 0.9, 0.08)),
                 cell(0, 0, "Item", True), cell(0, 1, "Details", True),
                 cell(1, 0, "Loan amount"), cell(1, 1, "Rs. 10,00,000"),
                 cell(2, 0, "Processing fee"), cell(2, 1, "2% of loan amount + GST"),
                 cell(3, 0, "Net disbursed amount"), cell(3, 1, "Rs. 9,57,900"),
                 Item("#/texts/2", 2, "section_header", "Terms and Conditions", (0.1, 0.05, 0.9, 0.08)),
                 Item("#/texts/3", 2, "list_item", "1. Prepayment. Prepayment attracts a charge of 4% of the principal "
                      "outstanding plus GST.", (0.1, 0.1, 0.9, 0.14)),
                 Item("#/texts/4", 2, "text", "after 6 EMIs only.", (0.1, 0.14, 0.9, 0.16)),
                 Item("#/texts/5", 2, "text", "Signed: ________ (Borrower)", (0.1, 0.8, 0.9, 0.82))]
        return ParsedDoc(items=items, ocr_lines=[], docling_json={"name": "kfs"}, scores={})


async def test_clauses_and_doc_terms(client, engine, fake_store):  # type: ignore[no-untyped-def]
    from app.llm.fakes import FakeLLMProvider

    h, sid = await ready_session(client)
    ag07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()
    did = (await upload(client, h, sid, [("ag07.pdf", ag07)])).json()["document_id"]
    llm = FakeLLMProvider([{"terms": [
        {"clause": "C1", "field": "prepayment_charge", "value_raw": "4% of the principal outstanding",
         "quantity": "percent", "includes_gst": False, "conditions": ["after 6 EMIs only"]},
        {"clause": "C1", "field": "penal_charge", "value_raw": "5% per month", "quantity": "percent"},  # not in the clause
        {"clause": "C9", "field": "emi", "value_raw": "Rs. 100", "quantity": "amount_inr"}]}])  # unknown ref
    await drain(engine, fake_store, KfsParser(), llm)
    assert (await doc(client, h, did))["status"] == "segmented"
    r = await client.get(f"/api/v1/documents/{did}/clauses", headers=h)
    assert r.status_code == 200, r.text
    cl = r.json()
    assert [c["label"] for c in cl] == ["table_row", "table_row", "table_row", "list_item", "signature"]
    assert cl[0]["text"] == "Item | Details\nLoan amount | Rs. 10,00,000" and cl[0]["section_path"] == "Key Fact Statement"
    assert cl[0]["is_table_row"] and len(cl[0]["bboxes"]) == 2 and cl[0]["bboxes"][0]["page"] == 1
    assert cl[3]["text"].endswith("plus GST.\nafter 6 EMIs only.") and cl[3]["section_path"] == "Terms and Conditions"
    terms = {t["field"]: t for c in cl for t in c["terms"]}
    assert set(terms) == {"loan_amount", "processing_fee", "net_disbursement", "prepayment_charge"}
    assert terms["loan_amount"]["value"] == "1000000" and terms["loan_amount"]["unit"] == "INR"
    assert terms["processing_fee"]["value"] == "2" and terms["processing_fee"]["includes_gst"] is False
    assert terms["net_disbursement"]["value"] == "957900" and terms["loan_amount"]["source"] == "lexicon"
    pre = terms["prepayment_charge"]
    assert (pre["value"], pre["unit"], pre["source"], pre["conditions"]) == ("4", "%", "llm", ["after 6 EMIs only"])
    assert [c["ord"] for c in (await client.get(f"/api/v1/documents/{did}/clauses?page=2", headers=h)).json()] == [3, 4]
    nv = await scalar(engine, "SELECT count(*) FROM doc_terms WHERE document_id = :d AND normaliser_version = 'norm-1.0'",
                      d=uuid.UUID(did))
    assert nv == 4

    # A re-run of the same version rewrites its rows instead of duplicating them (doc 05 §6.9).
    from app.worker import queue

    async with engine.begin() as c:
        await queue.enqueue(c, "doc.ingest", {"document_id": did, "version": 1}, dedupe_key=f"rerun:{did}")
    await drain(engine, fake_store, KfsParser())  # no LLM this time: lexicon terms only
    assert await scalar(engine, "SELECT count(*) FROM clauses WHERE document_id = :d", d=uuid.UUID(did)) == 5
    assert await scalar(engine, "SELECT count(*) FROM doc_terms WHERE document_id = :d", d=uuid.UUID(did)) == 3

    _, other_h = await register_and_login(client)
    assert (await client.get(f"/api/v1/documents/{did}/clauses", headers=other_h)).status_code == 404


async def test_llm_schema_failure_keeps_lexicon_terms(client, engine, fake_store):  # type: ignore[no-untyped-def]
    from app.core.retry import SchemaError

    class BrokenLLM:
        name = "broken"

        async def structured(self, **kw):  # type: ignore[no-untyped-def]
            raise SchemaError("bad json after corrective retry")

    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("kfs.pdf", pdf_bytes(2))])).json()["document_id"]
    await drain(engine, fake_store, KfsParser(), BrokenLLM())
    assert (await doc(client, h, did))["status"] == "segmented"
    assert await scalar(engine, "SELECT count(*) FROM doc_terms WHERE document_id = :d", d=uuid.UUID(did)) == 3
