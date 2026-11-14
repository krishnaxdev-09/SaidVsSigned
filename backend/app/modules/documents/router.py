"""Document endpoints (doc 05 §5, doc 11 §4). Upload, list, get, replace a photo page. Consent-gated. T2.1."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.retry import InputError
from app.core.storage import ObjectStore
from app.modules.audit import service as audit
from app.modules.consent.gate import require_consent
from app.modules.consent.service import has_active_consent
from app.modules.documents import media
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import get_owned_session
from app.worker import queue

router = APIRouter(prefix="/api/v1", tags=["documents"])

Kind = Literal["kfs", "loan_agreement", "sanction_letter", "fee_schedule", "other"]  # insurance_policy deferred
_STAGE = {"queued": "queued", "sanitising": "sanitising", "sanitised": "sanitising", "rendering": "rendering",
          "ocr": "ocr", "confidence": "confidence", "segmenting": "segmenting", "segmented": "segmenting",
          "failed": "failed"}


class UploadOut(BaseModel):
    document_id: uuid.UUID
    version: int
    status: str
    duplicate: bool


class PageOut(BaseModel):
    page: int
    width: float | None
    height: float | None
    text_source: str | None = None
    ocr_conf: float | None = None


class DocumentOut(BaseModel):
    document_id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    source: str
    version: int
    status: str
    stage: str
    status_code: str | None
    page_count: int
    warnings: list[str]
    pages: list[PageOut]
    created_at: datetime


class _Spooled:
    def __init__(self, path: Path, fmt: str, sha: str, size: int) -> None:
        self.path, self.fmt, self.sha, self.size = path, fmt, sha, size


async def _spool(files: list[UploadFile], tmp: Path) -> list[_Spooled]:
    s = get_settings()
    out: list[_Spooled] = []
    for i, f in enumerate(files, start=1):
        p = tmp / f"in{i}"
        h, size, head = hashlib.sha256(), 0, b""
        with p.open("wb") as w:
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > s.MAX_UPLOAD_MB_DOC * 1024 * 1024:
                    raise AppError("too_large", f"Each file must be at most {s.MAX_UPLOAD_MB_DOC} MB")
                head += chunk[: max(0, 16 - len(head))]
                h.update(chunk)
                w.write(chunk)
        fmt = media.sniff(head)
        if size == 0 or fmt is None:
            raise AppError("unsupported_media", "Upload a PDF or photos (JPEG, PNG, WebP)")
        out.append(_Spooled(p, fmt, h.hexdigest(), size))
    return out


def _validate(spooled: list[_Spooled]) -> tuple[str, int]:
    """(source, page_count) per doc 05 §4."""
    s = get_settings()
    if not spooled:
        raise AppError("validation_error", "No files")
    pdfs = [x for x in spooled if x.fmt == media.PDF]
    if pdfs:
        if len(spooled) > 1:
            raise AppError("validation_error", "Upload one PDF, or photos only (not both)")
        try:
            pages = media.pdf_page_count(pdfs[0].path)
        except InputError as exc:
            raise AppError(exc.code, str(exc)) from exc
        if pages > s.MAX_DOC_PAGES:
            raise AppError("too_many_pages", f"A PDF can have at most {s.MAX_DOC_PAGES} pages")
        return "pdf", pages
    if len(spooled) > s.MAX_PHOTO_PAGES:
        raise AppError("too_many_pages", f"Upload at most {s.MAX_PHOTO_PAGES} photos per document")
    if any(x.size > s.MAX_PHOTO_MB * 1024 * 1024 for x in spooled):
        raise AppError("too_large", f"Each photo must be at most {s.MAX_PHOTO_MB} MB")
    return "photos", len(spooled)


def content_sha(page_hashes: list[str]) -> str:
    """doc 05 §5: sha256 over the ordered page hashes."""
    return hashlib.sha256("\n".join(page_hashes).encode()).hexdigest()


def _store(request: Request) -> ObjectStore:
    return request.app.state.store  # type: ignore[no-any-return]


async def _enqueue(conn: AsyncConnection, session_id: uuid.UUID, doc_id: uuid.UUID, version: int) -> None:
    await queue.enqueue(conn, "doc.ingest", {"document_id": str(doc_id), "version": version}, session_id=session_id,
                        dedupe_key=f"doc.ingest:{doc_id}:{version}")
    await publish(conn, session_id, EventType.DOCUMENT_STATUS, {"document_id": str(doc_id), "version": version,
                                                               "stage": "queued", "progress": 0.0, "warnings": []})


async def load_document(conn: AsyncConnection, doc_id: uuid.UUID, user_id: uuid.UUID) -> DocumentOut:
    d = (await conn.execute(text(
        "SELECT d.* FROM documents d JOIN sessions s ON s.id = d.session_id "
        "WHERE d.id = :id AND s.owner_id = :u AND s.deleted_at IS NULL AND d.deleted_at IS NULL"),
        {"id": doc_id, "u": user_id})).mappings().first()
    if d is None:
        raise AppError("not_found", "Document not found")
    pages = (await conn.execute(text(
        "SELECT page, width, height, text_source, ocr_conf FROM document_pages WHERE document_id = :id AND version = :v "
        "ORDER BY page"),
        {"id": doc_id, "v": d["version"]})).all()
    return DocumentOut(document_id=d["id"], session_id=d["session_id"], kind=d["kind"], source=d["source"],
                       version=d["version"], status=d["status"], stage=_STAGE.get(d["status"], d["status"]),
                       status_code=d["status_code"], page_count=d["page_count"], warnings=list(d["warnings"] or []),
                       pages=[PageOut(page=p.page, width=p.width, height=p.height, text_source=p.text_source,
                                      ocr_conf=p.ocr_conf) for p in pages],
                       created_at=d["created_at"])


@router.post("/sessions/{session_id}/documents", status_code=202, response_model=UploadOut,
             responses={200: {"model": UploadOut, "description": "Duplicate of a document in this session"}})
async def upload_document(request: Request, session_id: uuid.UUID = Depends(require_consent), kind: Kind = Form(...),
                          files: list[UploadFile] = File(...), user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    with tempfile.TemporaryDirectory(prefix="ss-doc-") as d:
        spooled = await _spool(files, Path(d))
        source, page_count = _validate(spooled)
        sha = content_sha([x.sha for x in spooled])

        async def handler() -> tuple[int, UploadOut]:
            async with get_engine().begin() as conn:
                await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": session_id})
                dup = (await conn.execute(text(
                    "SELECT id, version, status FROM documents WHERE session_id = :s AND content_sha256 = :h "
                    "AND deleted_at IS NULL"), {"s": session_id, "h": sha})).first()
                if dup is not None:
                    return 200, UploadOut(document_id=dup.id, version=dup.version, status=dup.status, duplicate=True)
                doc_id = uuid7()
                await conn.execute(text(
                    "INSERT INTO documents (id, session_id, kind, source, version, content_sha256, status, page_count) "
                    "VALUES (:id, :s, :k, :src, 1, :h, 'queued', :n)"),
                    {"id": doc_id, "s": session_id, "k": kind, "src": source, "h": sha, "n": page_count})
                store = _store(request)
                if source == "pdf":
                    key = f"{doc_id}/v1/original/document.pdf"
                    await store.put_file("documents", key, str(spooled[0].path), media.CONTENT_TYPES[media.PDF])
                    rows = [(n, key, spooled[0].sha) for n in range(1, page_count + 1)]
                else:
                    rows = []
                    for n, x in enumerate(spooled, start=1):
                        key = f"{doc_id}/v1/original/{n}.{media.EXT[x.fmt]}"
                        await store.put_file("documents", key, str(x.path), media.CONTENT_TYPES[x.fmt])
                        rows.append((n, key, x.sha))
                for n, key, h in rows:
                    await conn.execute(text(
                        "INSERT INTO document_pages (document_id, version, page, original_key, original_sha256) "
                        "VALUES (:d, 1, :n, :k, :h)"), {"d": doc_id, "n": n, "k": key, "h": h})
                await audit.record(conn, "document.upload", actor_id=user_id, target_type="document", target_id=doc_id,
                                   ip=client_ip(request), meta={"session_id": str(session_id), "kind": kind,
                                                                "source": source, "pages": page_count, "sha256": sha})
                await _enqueue(conn, session_id, doc_id, 1)
            return 202, UploadOut(document_id=doc_id, version=1, status="queued", duplicate=False)

        canonical = json.dumps({"kind": kind, "sha256": sha}).encode()
        return await idempotent_create(request, user_id, 202, handler, body=canonical)


@router.get("/sessions/{session_id}/documents", response_model=list[DocumentOut])
async def list_documents(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> list[DocumentOut]:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        ids = (await conn.execute(text(
            "SELECT id FROM documents WHERE session_id = :s AND deleted_at IS NULL ORDER BY created_at"),
            {"s": session_id})).scalars().all()
        return [await load_document(conn, i, user_id) for i in ids]


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> DocumentOut:
    async with get_engine().connect() as conn:
        return await load_document(conn, document_id, user_id)


@router.put("/documents/{document_id}/pages/{page}", status_code=202, response_model=UploadOut)
async def replace_page(document_id: uuid.UUID, page: int, request: Request, file: UploadFile = File(...),
                       user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    """Replace one photo page (doc 05 §5): a new version with the other pages carried over; re-ingested.
    Re-verification with changed_because=document_replaced arrives with T2.5."""
    async with get_engine().connect() as conn:
        doc = await load_document(conn, document_id, user_id)
        if not await has_active_consent(conn, doc.session_id):
            raise AppError("consent_required", "Active customer consent and recording attestation are required")
    if doc.source != "photos":
        raise AppError("invalid_state", "Only photo documents can have a page replaced; upload a new PDF instead")
    if not 1 <= page <= doc.page_count:
        raise AppError("not_found", f"Page {page} does not exist")
    with tempfile.TemporaryDirectory(prefix="ss-doc-") as d:
        (new,) = await _spool([file], Path(d))
        if new.fmt == media.PDF:
            raise AppError("unsupported_media", "Replace a page with a photo (JPEG, PNG, WebP)")
        _validate([new])

        async def handler() -> tuple[int, UploadOut]:
            async with get_engine().begin() as conn:
                row = (await conn.execute(text(
                    "SELECT version, status FROM documents WHERE id = :id FOR UPDATE"), {"id": document_id})).first()
                assert row is not None
                if row.version != doc.version:
                    raise AppError("invalid_state", "The document changed; reload and try again")
                old = (await conn.execute(text(
                    "SELECT page, original_key, original_sha256 FROM document_pages WHERE document_id = :d AND version = :v "
                    "ORDER BY page"), {"d": document_id, "v": row.version})).all()
                v = row.version + 1
                key = f"{document_id}/v{v}/original/{page}.{media.EXT[new.fmt]}"
                await _store(request).put_file("documents", key, str(new.path), media.CONTENT_TYPES[new.fmt])
                pages: list[Any] = [(p.page, key, new.sha) if p.page == page else (p.page, p.original_key, p.original_sha256)
                                    for p in old]
                for n, k, h in pages:
                    await conn.execute(text(
                        "INSERT INTO document_pages (document_id, version, page, original_key, original_sha256) "
                        "VALUES (:d, :v, :n, :k, :h)"), {"d": document_id, "v": v, "n": n, "k": k, "h": h})
                await conn.execute(text(
                    "UPDATE documents SET version = :v, content_sha256 = :h, status = 'queued', status_code = NULL, "
                    "warnings = '[]' WHERE id = :id"), {"v": v, "h": content_sha([h for _, _, h in pages]), "id": document_id})
                await audit.record(conn, "document.page_replace", actor_id=user_id, target_type="document",
                                   target_id=document_id, ip=client_ip(request),
                                   meta={"session_id": str(doc.session_id), "page": page, "version": v})
                await _enqueue(conn, doc.session_id, document_id, v)
            return 202, UploadOut(document_id=document_id, version=v, status="queued", duplicate=False)

        canonical = json.dumps({"page": page, "sha256": new.sha}).encode()
        return await idempotent_create(request, user_id, 202, handler, body=canonical)


class PageImageOut(BaseModel):
    url: str
    expires_in: int


@router.get("/documents/{document_id}/pages/{page}/image", response_model=PageImageOut)
async def page_image(document_id: uuid.UUID, page: int, request: Request,
                     user_id: uuid.UUID = Depends(current_user)) -> PageImageOut:
    """Presigned URL (5 min) for the rendered page image (doc 05 §5)."""
    async with get_engine().connect() as conn:
        doc = await load_document(conn, document_id, user_id)
        key = (await conn.execute(text(
            "SELECT image_key FROM document_pages WHERE document_id = :d AND version = :v AND page = :n"),
            {"d": document_id, "v": doc.version, "n": page})).scalar()
    if key is None:
        raise AppError("not_found", "Page image not available yet")
    return PageImageOut(url=await _store(request).get_url("documents", key, 300), expires_in=300)


class TermOut(BaseModel):
    field: str
    value_raw: str
    value: str | None  # plain decimal string ("1000000", "17.5"), never scientific notation
    unit: str | None
    basis: str | None
    rate_type: str | None
    includes_gst: bool | None
    conditions: list[str]
    source: str | None


class ClauseOut(BaseModel):
    id: uuid.UUID
    ord: int
    page_start: int
    page_end: int
    label: str | None
    section_path: str | None
    text: str
    is_table_row: bool
    bboxes: list[dict[str, Any]]
    text_source: str | None
    ocr_conf: float | None
    ocr_min_numeric_conf: float | None
    terms: list[TermOut]


def _plain(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


@router.get("/documents/{document_id}/clauses", response_model=list[ClauseOut])
async def list_clauses(document_id: uuid.UUID, page: int | None = None,
                       user_id: uuid.UUID = Depends(current_user)) -> list[ClauseOut]:
    """Clauses of the current version (doc 05 §5), optionally only those on `page`, with their doc_terms."""
    async with get_engine().connect() as conn:
        doc = await load_document(conn, document_id, user_id)
        rows = (await conn.execute(text(
            "SELECT * FROM clauses WHERE document_id = :d AND version = :v "
            "AND (CAST(:p AS int) IS NULL OR :p BETWEEN page_start AND page_end) ORDER BY ord"),
            {"d": document_id, "v": doc.version, "p": page})).mappings().all()
        trows = (await conn.execute(text(
            "SELECT * FROM doc_terms WHERE document_id = :d AND version = :v"),
            {"d": document_id, "v": doc.version})).mappings().all()
    by: dict[uuid.UUID, list[TermOut]] = {}
    for t in trows:
        by.setdefault(t["clause_id"], []).append(TermOut(
            field=t["field"], value_raw=t["value_raw"], value=_plain(t["value"]), unit=t["unit"], basis=t["basis"],
            rate_type=t["rate_type"], includes_gst=t["includes_gst"], conditions=list(t["conditions"] or []),
            source=t["source"]))
    return [ClauseOut(id=r["id"], ord=r["ord"], page_start=r["page_start"], page_end=r["page_end"], label=r["label"],
                      section_path=r["section_path"], text=r["text"], is_table_row=bool(r["is_table_row"]),
                      bboxes=list(r["bboxes"] or []), text_source=r["text_source"], ocr_conf=r["ocr_conf"],
                      ocr_min_numeric_conf=r["ocr_min_numeric_conf"], terms=by.get(r["id"], [])) for r in rows]
