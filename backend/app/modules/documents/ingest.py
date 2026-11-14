"""Worker job `doc.ingest:{document_id}:{version}` (doc 05 §6). T2.1 implements step 1, sanitising; rendering,
OCR and later steps are added by T2.2+. Input failures end the document as failed(code) without retries."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import text

from app.core.retry import InputError
from app.core.storage import ObjectStore
from app.modules.documents import media
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.worker.runner import Handler, JobContext


def make_doc_ingest_handler(store: ObjectStore) -> Handler:
    async def handle(ctx: JobContext) -> None:
        doc_id = uuid.UUID(ctx.job.payload["document_id"])
        version = int(ctx.job.payload["version"])
        d = (await ctx.conn.execute(text(
            "SELECT session_id, source, version, status, deleted_at FROM documents WHERE id = :id"), {"id": doc_id})).first()
        if d is None or d.deleted_at is not None or d.version != version or d.status == "failed":
            return  # replaced by a newer version, erased or failed (e.g. consent revoked): nothing to do
        sid = d.session_id

        async def status(st: str, progress: float, code: str | None = None, warnings: list[str] | None = None) -> None:
            await ctx.conn.execute(text(
                "UPDATE documents SET status = :st, status_code = :c, warnings = CAST(:w AS jsonb) "
                "WHERE id = :id AND status <> 'failed'"), {"st": st, "c": code, "w": json.dumps(warnings or []), "id": doc_id})
            await publish(ctx.conn, sid, EventType.DOCUMENT_STATUS, {
                "document_id": str(doc_id), "version": version, "stage": "failed" if st == "failed" else "sanitising",
                "progress": progress, **({"code": code} if code else {}), "warnings": warnings or []})

        pages = (await ctx.conn.execute(text(
            "SELECT page, original_key FROM document_pages WHERE document_id = :d AND version = :v ORDER BY page"),
            {"d": doc_id, "v": version})).all()
        await ctx.checkpoint()
        try:
            with tempfile.TemporaryDirectory(prefix="ss-sanitise-") as tmp:
                t = Path(tmp)
                if d.source == "pdf":
                    src, dst = t / "in.pdf", t / "sanitised.pdf"
                    await store.get_file("documents", pages[0].original_key, str(src))
                    report = media.sanitise_pdf(src, dst)
                    await ctx.checkpoint()
                    await store.put_file("documents", f"{doc_id}/v{version}/sanitised/document.pdf", str(dst),
                                         media.CONTENT_TYPES[media.PDF])
                    warnings = [f"removed_{r}" for r in report.removed]
                else:
                    warnings = []
                    for p in pages:
                        src = t / f"p{p.page}"
                        await store.get_file("documents", p.original_key, str(src))
                        fmt = media.sniff(src.read_bytes()[:16])
                        if fmt is None or fmt == media.PDF:
                            raise InputError("not a photo", "unsupported_media")
                        dst = t / f"s{p.page}.{media.EXT[fmt]}"
                        rep = media.sanitise_image(src, dst, fmt)
                        await ctx.checkpoint()
                        await store.put_file("documents", f"{doc_id}/v{version}/sanitised/{p.page}.{media.EXT[fmt]}",
                                             str(dst), media.CONTENT_TYPES[fmt])
                        await ctx.conn.execute(text(
                            "UPDATE document_pages SET width = :w, height = :h WHERE document_id = :d AND version = :v "
                            "AND page = :n"), {"w": rep.width, "h": rep.height, "d": doc_id, "v": version, "n": p.page})
                        if rep.had_exif:
                            warnings.append("removed_exif")
                    warnings = sorted(set(warnings))
        except InputError as exc:
            await status("failed", 1.0, exc.code)
            return
        await ctx.checkpoint()
        await status("sanitised", 1.0, None, warnings)
        # Rendering, photo pre-processing and OCR (doc 05 §6 steps 2–5) are T2.2.

    return handle
