"""Live microphone capture — "record live, verify on stop" (user request 2026-11-15).

POST /sessions/{id}/live (consent required, idempotent) → {live_id}. The browser sends MediaRecorder chunks in order:
PUT /live/{live_id}/chunks/{seq} (raw bytes, ≤ 2 MB each; re-sending a seq replaces it, so retries are safe).
POST /live/{live_id}/finish {chunks: n} assembles chunks 0..n-1 into one file and hands it to the SAME pipeline as an
upload (audio.ingest → stt.batch → speakers → claims → verify), with audio_streams.source = 'live_mic'.
DELETE /live/{live_id} aborts and deletes the chunks. Nothing about uploads changes.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.modules.audio.router import UploadOut, _create_stream, _store
from app.modules.audio.service import SpooledUpload, current_stream, sniff_format
from app.modules.audit import service as audit
from app.modules.consent.gate import require_consent
from app.modules.consent.service import has_active_consent

router = APIRouter(prefix="/api/v1", tags=["live"])
MAX_CHUNK_BYTES = 2 * 1024 * 1024


class LiveOut(BaseModel):
    live_id: uuid.UUID
    status: str
    chunks: int = 0
    bytes: int = 0


class FinishIn(BaseModel):
    chunks: int = Field(ge=1, le=100_000)


async def _owned(conn: AsyncConnection, live_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False):  # type: ignore[no-untyped-def]
    row = (await conn.execute(text(
        "SELECT l.* FROM live_captures l JOIN sessions s ON s.id = l.session_id WHERE l.id = :id AND s.owner_id = :u "
        "AND s.deleted_at IS NULL" + (" FOR UPDATE OF l" if for_update else "")), {"id": live_id, "u": user_id})
    ).mappings().first()
    if row is None:
        raise AppError("not_found", "Live recording not found")
    return row


@router.post("/sessions/{session_id}/live", status_code=201, response_model=LiveOut)
async def start_live(request: Request, session_id: uuid.UUID = Depends(require_consent),
                     user_id: uuid.UUID = Depends(current_user)):  # type: ignore[no-untyped-def]
    async def handler() -> LiveOut:
        async with get_engine().begin() as conn:
            cur = await current_stream(conn, session_id)
            if cur is not None and cur.status != "failed":
                raise AppError("invalid_state", "This session already has a recording; replace it instead")
            lid = uuid.uuid4()
            await conn.execute(text("INSERT INTO live_captures (id, session_id, status) VALUES (:id, :s, 'open')"),
                               {"id": lid, "s": session_id})
            return LiveOut(live_id=lid, status="open")

    return await idempotent_create(request, user_id, 201, handler)


@router.put("/live/{live_id}/chunks/{seq}", response_model=LiveOut)
async def put_chunk(live_id: uuid.UUID, seq: int, request: Request, user_id: uuid.UUID = Depends(current_user)) -> LiveOut:
    if seq < 0 or seq > 100_000:
        raise AppError("validation_error", "Chunk number out of range")
    body = await request.body()
    if not body:
        raise AppError("validation_error", "Empty chunk")
    if len(body) > MAX_CHUNK_BYTES:
        raise AppError("too_large", "Chunk exceeds 2 MB")
    async with get_engine().begin() as conn:
        live = await _owned(conn, live_id, user_id, for_update=True)
        if live["status"] != "open":
            raise AppError("invalid_state", "This live recording is no longer open")
        if not await has_active_consent(conn, live["session_id"]):
            raise AppError("consent_required", "Consent was revoked")
        total = int((await conn.execute(text("SELECT coalesce(sum(size), 0) FROM live_chunks WHERE live_id = :l AND seq <> :q"),
                                        {"l": live_id, "q": seq})).scalar() or 0) + len(body)
        if total > get_settings().MAX_UPLOAD_MB_AUDIO * 1024 * 1024:
            raise AppError("too_large", f"Recording exceeds {get_settings().MAX_UPLOAD_MB_AUDIO} MB")
        key = f"{live['session_id']}/live/{live_id}/{seq:06d}"
        with tempfile.TemporaryDirectory(prefix="ss-live-") as t:
            p = Path(t) / "c"
            p.write_bytes(body)
            await _store(request).put_file("audio", key, str(p), "application/octet-stream")
        await conn.execute(text(
            "INSERT INTO live_chunks (live_id, seq, size, sha256, object_key) VALUES (:l, :q, :n, :h, :k) "
            "ON CONFLICT (live_id, seq) DO UPDATE SET size = :n, sha256 = :h, object_key = :k"),
            {"l": live_id, "q": seq, "n": len(body), "h": hashlib.sha256(body).hexdigest(), "k": key})
        n = (await conn.execute(text("SELECT count(*) FROM live_chunks WHERE live_id = :l"), {"l": live_id})).scalar()
        await conn.execute(text("UPDATE live_captures SET bytes = :b WHERE id = :l"), {"b": total, "l": live_id})
    return LiveOut(live_id=live_id, status="open", chunks=int(n or 0), bytes=int(total))


@router.post("/live/{live_id}/finish", status_code=202, response_model=UploadOut)
async def finish_live(live_id: uuid.UUID, body: FinishIn, request: Request,
                      user_id: uuid.UUID = Depends(current_user)) -> UploadOut:
    store = _store(request)
    async with get_engine().begin() as conn:
        live = await _owned(conn, live_id, user_id, for_update=True)
        if live["status"] == "finished" and live["audio_stream_id"]:
            return UploadOut(recording_id=live["audio_stream_id"], status="queued", duplicate=True)
        if live["status"] != "open":
            raise AppError("invalid_state", "This live recording is no longer open")
        sid = live["session_id"]
        if not await has_active_consent(conn, sid):
            raise AppError("consent_required", "Consent was revoked")
        chunks = (await conn.execute(text("SELECT seq, object_key FROM live_chunks WHERE live_id = :l ORDER BY seq"),
                                     {"l": live_id})).all()
        if [c.seq for c in chunks] != list(range(body.chunks)):
            have = {c.seq for c in chunks}
            missing = [i for i in range(body.chunks) if i not in have][:10]
            raise AppError("validation_error", f"Missing chunks {missing}; re-send them and finish again")
        tmp = tempfile.TemporaryDirectory(prefix="ss-live-")
        try:
            out = Path(tmp.name) / "original"
            h, size = hashlib.sha256(), 0
            with out.open("wb") as f:  # MediaRecorder timeslices are consecutive pieces of one stream
                for c in chunks:
                    part = Path(tmp.name) / f"p{c.seq}"
                    await store.get_file("audio", c.object_key, str(part))
                    data = part.read_bytes()
                    part.unlink()
                    h.update(data)
                    size += len(data)
                    f.write(data)
            with out.open("rb") as f:
                fmt = sniff_format(f.read(16))
            if fmt is None:
                raise AppError("unsupported_media", "The live recording isn't a supported audio format")
            up = SpooledUpload(out, h.hexdigest(), size, fmt, "live-recording", tmp)
            await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": sid})
            cur = await current_stream(conn, sid, for_update=True)
            if cur is not None and cur.status != "failed":
                raise AppError("invalid_state", "This session already has a recording")
            if cur is not None:
                await conn.execute(text("UPDATE audio_streams SET deleted_at = now() WHERE id = :id"), {"id": cur.id})
            stream_id = await _create_stream(conn, store, sid, up, "mono", None)
        finally:
            tmp.cleanup()
        await conn.execute(text("UPDATE audio_streams SET source = 'live_mic' WHERE id = :id"), {"id": stream_id})
        await conn.execute(text("UPDATE live_captures SET status = 'finished', finished_at = now(), audio_stream_id = :a "
                                "WHERE id = :l"), {"a": stream_id, "l": live_id})
        await audit.record(conn, "recording.upload", actor_id=user_id, target_type="recording", target_id=stream_id,
                           ip=client_ip(request), meta={"session_id": str(sid), "source": "live_mic", "size": size,
                                                         "chunks": body.chunks, "format": fmt})
    for c in chunks:  # the assembled original is stored; the pieces are no longer needed
        await store.delete("audio", c.object_key)
    return UploadOut(recording_id=stream_id, status="queued", duplicate=False)


@router.delete("/live/{live_id}", status_code=204, response_class=Response)
async def abort_live(live_id: uuid.UUID, request: Request, user_id: uuid.UUID = Depends(current_user)) -> Response:
    async with get_engine().begin() as conn:
        live = await _owned(conn, live_id, user_id, for_update=True)
        if live["status"] != "open":
            return Response(status_code=204)
        keys = (await conn.execute(text("SELECT object_key FROM live_chunks WHERE live_id = :l"), {"l": live_id})).scalars().all()
        await conn.execute(text("UPDATE live_captures SET status = 'aborted', finished_at = now() WHERE id = :l"),
                           {"l": live_id})
        await conn.execute(text("DELETE FROM live_chunks WHERE live_id = :l"), {"l": live_id})
    for k in keys:
        await _store(request).delete("audio", k)
    return Response(status_code=204)
