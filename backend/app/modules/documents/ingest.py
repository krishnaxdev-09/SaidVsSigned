"""Worker job `doc.ingest:{document_id}:{version}` (doc 05 §6). T2.1: step 1 sanitising. T2.2: steps 2–5 render,
photo pre-process, Docling/OCR, confidence pass (`parse.py`); outputs pages/{n}.webp, docling.json and items.json.
T2.3: steps 6–7 clause segmentation (`segment.py`) and doc_terms (`terms.py`), written atomically per
(document_id, version). T2.4: step 8 chunks + embeddings (`rag/retrieve.py`), same transaction, final status
`ready`.
Input failures end the document as failed(code)."""

from __future__ import annotations

import io
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import structlog
import yaml
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.retry import InputError
from app.core.storage import ObjectStore
from app.llm.base import EmbeddingProvider, LLMProvider
from app.modules.documents import media, parse, segment, terms
from app.modules.rag import retrieve
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.worker.runner import Handler, JobContext

log = structlog.get_logger()


def _t_ocr() -> float:
    return float(yaml.safe_load(get_settings().THRESHOLDS_FILE.read_text())["T_ocr"])  # doc 08 §5


async def _progress(engine: AsyncEngine, sid: uuid.UUID, doc_id: uuid.UUID, version: int, stage: str, p: float) -> None:
    """Committed on its own so the UI sees progress during slow OCR; never overwrites 'failed'."""
    async with engine.begin() as c:
        moved = (await c.execute(text("UPDATE documents SET status = :st WHERE id = :id AND version = :v AND status <> 'failed'"),
                                 {"st": stage, "id": doc_id, "v": version})).rowcount
        if moved:
            await publish(c, sid, EventType.DOCUMENT_STATUS, {"document_id": str(doc_id), "version": version,
                                                              "stage": stage, "progress": p, "warnings": []})


def make_doc_ingest_handler(store: ObjectStore, parser: parse.DocumentParser | None = None,
                            llm: LLMProvider | None = None, embedder: EmbeddingProvider | None = None) -> Handler:
    state: dict[str, Any] = {}

    def get_parser() -> parse.DocumentParser:
        if parser is not None:
            return parser
        state.setdefault("p", parse.DoclingParser())  # lazy: loading Docling models is slow
        return state["p"]

    def get_embedder() -> EmbeddingProvider:
        if embedder is not None:
            return embedder
        if "e" not in state:
            from app.llm.embeddings import from_settings as embedder_from_settings

            state["e"] = embedder_from_settings()  # ADR-005 working choice (HC-4)
        return state["e"]

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
            stage = {"failed": "failed", "ready": "ready"}.get(st, "sanitising")
            await publish(ctx.conn, sid, EventType.DOCUMENT_STATUS, {
                "document_id": str(doc_id), "version": version, "stage": stage,
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
                await ctx.checkpoint()
                page_warnings, parsed = await _render_and_parse(ctx, store, get_parser(), sid, doc_id, version,
                                                                d.source, [p.page for p in pages], t)
                warnings += page_warnings
        except InputError as exc:
            await status("failed", 1.0, exc.code)
            return
        await ctx.checkpoint()
        await _progress(ctx.engine, sid, doc_id, version, "segmenting", 0.9)
        clauses = segment.segment(parsed.items)
        doc_terms = terms.lexicon_terms(clauses)
        if llm is not None:
            llm_out, stats = await terms.llm_terms(llm, clauses, {t.clause_ord for t in doc_terms})
            doc_terms += llm_out
            log.info("doc_terms.llm", document_id=str(doc_id), version=version, **stats)
        await ctx.checkpoint()
        await _progress(ctx.engine, sid, doc_id, version, "indexing", 0.95)
        chunks = [(c, piece) for c in clauses for piece in retrieve.chunk_texts(c.text)]
        emb = get_embedder()
        vectors = await emb.embed([((c.section_path + "\n") if c.section_path else "") + piece for c, piece in chunks],
                                  kind="passage") if chunks else []
        await ctx.checkpoint()
        ids = await _write_clauses(ctx, doc_id, version, clauses, doc_terms)
        for (c, piece), vec in zip(chunks, vectors, strict=True):
            await ctx.conn.execute(text(
                "INSERT INTO chunks (id, document_id, version, clause_id, page, section_path, text, embedding, "
                "embedding_model) VALUES (:id, :d, :v, :c, :p, :sp, :t, CAST(:e AS vector), :m)"),
                {"id": uuid.uuid4(), "d": doc_id, "v": version, "c": ids[c.ord], "p": c.page_start,
                 "sp": c.section_path, "t": piece, "e": "[" + ",".join(f"{x:.6f}" for x in vec) + "]", "m": emb.name})
        await status("ready", 1.0, None, warnings)
        from app.modules.verify.job import enqueue_session  # doc 05 §6.9: verify every active claim

        await enqueue_session(ctx.conn, sid, "document_ready" if version == 1 else "document_replaced")

    return handle


async def _render_and_parse(ctx: JobContext, store: ObjectStore, parser: parse.DocumentParser, sid: uuid.UUID,
                            doc_id: uuid.UUID, version: int, source: str, page_nos: list[int],
                            t: Path) -> tuple[list[str], parse.ParsedDoc]:
    """doc 05 §6 steps 2–5. Returns page warnings (`blurry_page:N`, `page_unreadable:N`, `low_ocr_page:N`) and the
    parsed document."""
    prefix = f"{doc_id}/v{version}"
    await _progress(ctx.engine, sid, doc_id, version, "rendering", 0.2)
    quality: dict[int, dict[str, object]] = {}
    warnings: list[str] = []
    if source == "pdf":
        pdf_path = t / "sanitised.pdf"
        rendered = parse.render_pdf(pdf_path)
        images = [im for im, _ in rendered]
        ocr_pages = {n for n, (_, chars) in enumerate(rendered, start=1) if chars < parse.TEXT_LAYER_MIN_CHARS}
        for n, (_, chars) in enumerate(rendered, start=1):
            quality[n] = {"text_layer_chars": chars}
        force = False
    else:
        images = []
        for n in page_nos:
            src = next(p for p in t.iterdir() if p.name.startswith(f"s{n}."))
            pre = parse.preprocess_photo(Image.open(src))
            images.append(pre.image)
            quality[n] = {"blur": round(pre.blur, 1), "perspective_fixed": pre.perspective_fixed,
                          "deskew_deg": round(pre.deskew_deg, 2)}
            if pre.blur < parse.BLUR_MIN_VARIANCE:
                warnings.append(f"blurry_page:{n}")
        pdf_path = t / "photos.pdf"
        parse.images_to_pdf(images, pdf_path)
        ocr_pages, force = set(page_nos), True
    for n, im in enumerate(images, start=1):
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=85)
        (t / f"page{n}.webp").write_bytes(buf.getvalue())
        await store.put_file("documents", f"{prefix}/pages/{n}.webp", str(t / f"page{n}.webp"), "image/webp")
    await ctx.checkpoint()
    await _progress(ctx.engine, sid, doc_id, version, "ocr", 0.5)
    parsed = parser.parse(pdf_path, ocr=bool(ocr_pages), force_full_page_ocr=force)
    await ctx.checkpoint()
    await _progress(ctx.engine, sid, doc_id, version, "confidence", 0.8)
    parse.confidence_pass(parsed, ocr_pages)
    t_ocr = _t_ocr()
    for n, im in enumerate(images, start=1):
        lines = [ln for ln in parsed.ocr_lines if ln.page == n]
        page_conf = (sum(ln.confidence for ln in lines) / len(lines)) if lines else (None if n in ocr_pages else 1.0)
        if n in ocr_pages:
            if sum(len(ln.text) for ln in lines) < parse.UNREADABLE_MIN_CHARS:
                warnings.append(f"page_unreadable:{n}")
            elif page_conf is not None and page_conf < t_ocr:
                warnings.append(f"low_ocr_page:{n}")
        await ctx.conn.execute(text(
            "UPDATE document_pages SET image_key = :k, width = :w, height = :h, text_source = :ts, ocr_conf = :c, "
            "quality = CAST(:q AS jsonb) WHERE document_id = :d AND version = :v AND page = :n"),
            {"k": f"{prefix}/pages/{n}.webp", "w": im.width, "h": im.height,
             "ts": "ocr" if n in ocr_pages else "text_layer", "c": page_conf, "q": json.dumps(quality.get(n, {})),
             "d": doc_id, "v": version, "n": n})
    for name, payload in (("docling.json", parsed.docling_json),
                          ("items.json", {"parser": parser.name, "scores": parsed.scores, "items": parse.items_json(parsed)})):
        (t / name).write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        await store.put_file("documents", f"{prefix}/{name}", str(t / name), "application/json")
    return warnings, parsed


async def _write_clauses(ctx: JobContext, doc_id: uuid.UUID, version: int, clauses: list[segment.Clause],
                         doc_terms: list[terms.Term]) -> dict[int, uuid.UUID]:
    """doc 05 §6.9: a re-run rewrites this version's rows (same transaction as the final status)."""
    await ctx.conn.execute(text("DELETE FROM clauses WHERE document_id = :d AND version = :v"), {"d": doc_id, "v": version})
    ids: dict[int, uuid.UUID] = {}
    for c in clauses:
        ids[c.ord] = uuid.uuid4()
        await ctx.conn.execute(text(
            "INSERT INTO clauses (id, document_id, version, page_start, page_end, bboxes, section_path, label, text, "
            "is_table_row, text_source, ocr_conf, ocr_min_numeric_conf, ord) VALUES (:id, :d, :v, :ps, :pe, "
            "CAST(:bb AS jsonb), :sp, :l, :t, :tr, :ts, :c, :cn, :o)"),
            {"id": ids[c.ord], "d": doc_id, "v": version, "ps": c.page_start, "pe": c.page_end,
             "bb": json.dumps(c.bboxes), "sp": c.section_path, "l": c.label, "t": c.text, "tr": c.is_table_row,
             "ts": c.text_source, "c": c.ocr_conf, "cn": c.ocr_min_numeric_conf, "o": c.ord})
    for tm in doc_terms:
        await ctx.conn.execute(text(
            "INSERT INTO doc_terms (id, document_id, version, clause_id, field, value_raw, value, unit, basis, "
            "rate_type, conditions, includes_gst, normaliser_version, source) VALUES (:id, :d, :v, :c, :f, :raw, "
            ":val, :u, :b, :rt, CAST(:cond AS jsonb), :g, :nv, :src)"),
            {"id": uuid.uuid4(), "d": doc_id, "v": version, "c": ids[tm.clause_ord], "f": tm.field,
             "raw": tm.value_raw, "val": tm.value, "u": tm.unit, "b": tm.basis, "rt": tm.rate_type,
             "cond": json.dumps(tm.conditions), "g": tm.includes_gst, "nv": tm.normaliser_version, "src": tm.source})
    return ids
