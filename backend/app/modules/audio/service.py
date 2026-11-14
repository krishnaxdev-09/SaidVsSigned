"""Recording (audio_streams) persistence and shared logic for T1.1 (doc 02, doc 11 §3)."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError
from app.modules.audio.media import sniff_format

ChannelLayout = Literal["mono", "stereo_split"]
Stage = Literal["uploaded", "validating", "normalising", "transcribing", "diarizing", "ready", "failed"]

# DB status -> canonical event stage (doc 11 §5). 'normalised' = ingest done, waiting for STT (T1.2).
_STAGE: dict[str, Stage] = {"uploaded": "uploaded", "validating": "validating", "normalising": "normalising",
                            "normalised": "normalising", "transcribing": "transcribing", "diarizing": "diarizing",
                            "ready": "ready", "failed": "failed"}
PLAYABLE = ("normalised", "transcribing", "diarizing", "ready")


def object_prefix(session_id: uuid.UUID, stream_id: uuid.UUID) -> str:
    return f"{session_id}/{stream_id}"  # bucket 'audio' (doc 11 §6)


class Recording(BaseModel):
    recording_id: uuid.UUID
    session_id: uuid.UUID
    status: str
    stage: Stage
    status_code: str | None
    duration_ms: int | None
    format: str | None
    channel_layout: ChannelLayout | None
    warnings: list[str]
    replaced_by: uuid.UUID | None
    created_at: datetime


COLUMNS = ("id, session_id, status, status_code, duration_ms, detected_format, channel_layout, warnings, "
           "replaced_by, created_at, sha256, deleted_at, object_key_original")


def to_recording(r: Any) -> Recording:
    return Recording(recording_id=r.id, session_id=r.session_id, status=r.status, stage=_STAGE.get(r.status, "failed"),
                     status_code=r.status_code, duration_ms=r.duration_ms, format=r.detected_format,
                     channel_layout=r.channel_layout, warnings=list(r.warnings or []), replaced_by=r.replaced_by,
                     created_at=r.created_at)


async def get_owned_recording(conn: AsyncConnection, recording_id: uuid.UUID, user_id: uuid.UUID) -> Any:
    row = (await conn.execute(text(
        f"SELECT {', '.join('a.' + c.strip() for c in COLUMNS.split(','))} FROM audio_streams a "
        "JOIN sessions s ON s.id = a.session_id "
        "WHERE a.id = :id AND s.owner_id = :u AND s.deleted_at IS NULL"),
        {"id": recording_id, "u": user_id})).first()
    if row is None:
        raise AppError("not_found", "Recording not found")
    return row


async def current_stream(conn: AsyncConnection, session_id: uuid.UUID, *, for_update: bool = False) -> Any:
    lock = " FOR UPDATE" if for_update else ""
    return (await conn.execute(text(
        f"SELECT {COLUMNS} FROM audio_streams WHERE session_id = :s AND deleted_at IS NULL "
        f"ORDER BY created_at DESC LIMIT 1{lock}"), {"s": session_id})).first()


@dataclass
class SpooledUpload:
    """An upload copied to a private temp file while hashing it and enforcing the size limit (doc 02 §5 step 1)."""
    path: Path
    sha256: str
    size: int
    fmt: str
    filename: str | None
    _dir: tempfile.TemporaryDirectory[str]

    def cleanup(self) -> None:
        self._dir.cleanup()


async def spool_upload(file: UploadFile, max_bytes: int) -> SpooledUpload:
    tmp = tempfile.TemporaryDirectory(prefix="ss-upload-")
    path = Path(tmp.name) / "original"
    h = hashlib.sha256()
    size = 0
    head = b""
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise AppError("too_large", f"Recording exceeds {max_bytes // (1024 * 1024)} MB")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                h.update(chunk)
                out.write(chunk)
        fmt = sniff_format(head)
        if size == 0 or fmt is None:
            raise AppError("unsupported_media", "Unsupported or unrecognised audio format")
    except BaseException:
        tmp.cleanup()
        raise
    return SpooledUpload(path, h.hexdigest(), size, fmt, (file.filename or "")[:255] or None, tmp)
