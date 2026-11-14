"""Recording endpoints (doc 02 §4, doc 11 §4). All processing endpoints are consent-gated (doc 01 §5.5)."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.storage import ObjectStore
from app.modules.audio import media
from app.modules.audio.service import (
    PLAYABLE,
    ChannelLayout,
    Recording,
    SpooledUpload,
    current_stream,
    get_owned_recording,
    object_prefix,
    spool_upload,
    to_recording,
)
from app.modules.audit import service as audit
from app.modules.consent.gate import require_consent
from app.modules.consent.service import has_active_consent
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.worker import queue

router = APIRouter(prefix="/api/v1", tags=["recordings"])

MAX_SNIPPET_MS = 10 * 60 * 1000


class UploadOut(BaseModel):
    recording_id: uuid.UUID
    status: str
    duplicate: bool


class SnippetOut(BaseModel):
    url: str
    from_ms: int
    to_ms: int
    expires_in: int


def _store(request: Request) -> ObjectStore:
    return request.app.state.store  # type: ignore[no-any-return]


def _precheck_length(request: Request) -> int:
    max_bytes = get_settings().MAX_UPLOAD_MB_AUDIO * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + 1024 * 1024:  # multipart overhead allowance
        raise AppError("too_large", f"Recording exceeds {get_settings().MAX_UPLOAD_MB_AUDIO} MB")
    return max_bytes


def _canonical(up: SpooledUpload, channel_layout: str | None, recorded_on: date | None) -> bytes:
    return json.dumps({"sha256": up.sha256, "channel_layout": channel_layout,
                       "recorded_on": recorded_on.isoformat() if recorded_on else None}).encode()


async def _create_stream(conn: AsyncConnection, store: ObjectStore, session_id: uuid.UUID, up: SpooledUpload,
                         channel_layout: str | None, recorded_on: date | None) -> uuid.UUID:
    """Store the original, insert the row, enqueue ingest, move the session to processing, publish the status."""
    stream_id = uuid7()
    key = f"{object_prefix(session_id, stream_id)}/original"
    await store.put_file("audio", key, str(up.path), media.CONTENT_TYPES[up.fmt])
    await conn.execute(text(
        "INSERT INTO audio_streams (id, session_id, original_filename, detected_format, sha256, size_bytes, "
        "channel_layout, status, object_key_original, warnings) "
        "VALUES (:id, :s, :fn, :fmt, :sha, :size, :cl, 'uploaded', :key, CAST(:w AS jsonb))"),
        {"id": stream_id, "s": session_id, "fn": up.filename, "fmt": up.fmt, "sha": up.sha256, "size": up.size,
         "cl": channel_layout or "mono", "key": key,
         "w": json.dumps([f"recorded_on:{recorded_on.isoformat()}"] if recorded_on else [])})
    await queue.enqueue(conn, "audio.ingest", {"audio_stream_id": str(stream_id)}, session_id=session_id,
                        dedupe_key=f"audio.ingest:{stream_id}")
    await conn.execute(text("UPDATE sessions SET state = 'processing', state_reason = NULL WHERE id = :s"),
                       {"s": session_id})
    await publish(conn, session_id, EventType.RECORDING_STATUS,
                  {"recording_id": str(stream_id), "stage": "uploaded", "progress": 0.0})
    await publish(conn, session_id, EventType.SESSION_STATUS, {"state": "processing"})
    return stream_id


@router.post("/sessions/{session_id}/recordings", status_code=202, response_model=UploadOut,
             responses={200: {"model": UploadOut, "description": "Duplicate of the session's current recording"}})
async def upload_recording(request: Request, session_id: uuid.UUID = Depends(require_consent),
                           file: UploadFile = File(...), channel_layout: ChannelLayout | None = Form(None),
                           recorded_on: date | None = Form(None),
                           user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    max_bytes = _precheck_length(request)
    up = await spool_upload(file, max_bytes)
    try:
        async def handler() -> tuple[int, UploadOut]:
            async with get_engine().begin() as conn:
                # Serialise uploads per session (one recording per session, doc 02 §3).
                await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": session_id})
                cur = await current_stream(conn, session_id, for_update=True)
                if cur is not None and cur.sha256 == up.sha256:
                    return 200, UploadOut(recording_id=cur.id, status=cur.status, duplicate=True)
                if cur is not None and cur.status != "failed":
                    raise AppError("invalid_state", "This session already has a recording; use "
                                   f"PUT /api/v1/recordings/{cur.id} to replace it")
                if cur is not None:  # a failed recording may be superseded by a new upload
                    await conn.execute(text("UPDATE audio_streams SET deleted_at = now() WHERE id = :id"),
                                       {"id": cur.id})
                stream_id = await _create_stream(conn, _store(request), session_id, up, channel_layout, recorded_on)
                await audit.record(conn, "recording.upload", actor_id=user_id, target_type="recording",
                                   target_id=stream_id, ip=client_ip(request),
                                   meta={"session_id": str(session_id), "sha256": up.sha256, "size": up.size,
                                         "format": up.fmt})
            return 202, UploadOut(recording_id=stream_id, status="queued", duplicate=False)

        return await idempotent_create(request, user_id, 202, handler,
                                       body=_canonical(up, channel_layout, recorded_on))
    finally:
        up.cleanup()


@router.put("/recordings/{recording_id}", status_code=202, response_model=UploadOut)
async def replace_recording(recording_id: uuid.UUID, request: Request, file: UploadFile = File(...),
                            channel_layout: ChannelLayout | None = Form(None), recorded_on: date | None = Form(None),
                            user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    """Replace the session's recording (doc 02 §4). The old stream is soft-deleted and linked to the new one.
    The transcript revision and claim supersession that follow a replace are added with T1.2 / T1.6."""
    async with get_engine().connect() as conn:
        old = await get_owned_recording(conn, recording_id, user_id)
        if not await has_active_consent(conn, old.session_id):
            raise AppError("consent_required", "Active customer consent and recording attestation are required")
    session_id = old.session_id
    max_bytes = _precheck_length(request)
    up = await spool_upload(file, max_bytes)
    try:
        async def handler() -> tuple[int, UploadOut]:
            async with get_engine().begin() as conn:
                await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": session_id})
                cur = await current_stream(conn, session_id, for_update=True)
                if cur is None or cur.id != recording_id:
                    raise AppError("invalid_state", "Only the session's current recording can be replaced")
                if cur.sha256 == up.sha256:
                    return 200, UploadOut(recording_id=cur.id, status=cur.status, duplicate=True)
                await conn.execute(text("UPDATE audio_streams SET deleted_at = now() WHERE id = :id"),
                                   {"id": recording_id})
                stream_id = await _create_stream(conn, _store(request), session_id, up, channel_layout, recorded_on)
                await conn.execute(text("UPDATE audio_streams SET replaced_by = :n WHERE id = :o"),
                                   {"n": stream_id, "o": recording_id})
                await audit.record(conn, "recording.replace", actor_id=user_id, target_type="recording",
                                   target_id=stream_id, ip=client_ip(request),
                                   meta={"session_id": str(session_id), "replaces": str(recording_id),
                                         "sha256": up.sha256})
            return 202, UploadOut(recording_id=stream_id, status="queued", duplicate=False)

        return await idempotent_create(request, user_id, 202, handler,
                                       body=_canonical(up, channel_layout, recorded_on))
    finally:
        up.cleanup()


@router.get("/recordings/{recording_id}", response_model=Recording)
async def get_recording(recording_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> Recording:
    async with get_engine().connect() as conn:
        return to_recording(await get_owned_recording(conn, recording_id, user_id))


@router.get("/sessions/{session_id}/recording", response_model=Recording | None)
async def get_session_recording(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> Recording | None:
    """The session's current recording, or null (used by the session page)."""
    from app.modules.sessions.service import get_owned_session

    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        cur = await current_stream(conn, session_id)
    return to_recording(cur) if cur else None


@router.get("/sessions/{session_id}/audio", response_model=SnippetOut)
async def audio_snippet(request: Request, session_id: uuid.UUID = Depends(require_consent),
                        from_ms: int = Query(ge=0), to_ms: int = Query(gt=0)) -> SnippetOut:
    """Presigned URL (5 min) for a snippet cut server-side from the normalised FLAC (doc 02 §4)."""
    if to_ms <= from_ms or to_ms - from_ms > MAX_SNIPPET_MS:
        raise AppError("validation_error", f"Need from_ms < to_ms and a range of at most {MAX_SNIPPET_MS} ms")
    async with get_engine().connect() as conn:
        cur = await current_stream(conn, session_id)
    if cur is None or cur.status not in PLAYABLE:
        raise AppError("invalid_state", "The recording is not ready for playback")
    to_ms = min(to_ms, cur.duration_ms or to_ms)
    if from_ms >= to_ms:
        raise AppError("validation_error", "Range is outside the recording")
    store = _store(request)
    prefix = object_prefix(session_id, cur.id)
    key = f"{prefix}/snippets/{from_ms}-{to_ms}.flac"  # deterministic: repeated requests reuse the object
    if not await store.exists("audio", key):
        s = get_settings()
        with tempfile.TemporaryDirectory(prefix="ss-snip-") as d:
            src, dst = Path(d) / "normalized.flac", Path(d) / "snippet.flac"
            await store.get_file("audio", f"{prefix}/normalized.flac", str(src))
            await media.cut_snippet(src, dst, from_ms, to_ms, s.FFMPEG_TIMEOUT_S)
            await store.put_file("audio", key, str(dst), "audio/flac")
    return SnippetOut(url=await store.get_url("audio", key, 300), from_ms=from_ms, to_ms=to_ms, expires_in=300)
