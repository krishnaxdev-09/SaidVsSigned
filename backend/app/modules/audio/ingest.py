"""Worker job `audio.ingest:{audio_stream_id}` (doc 02 §5 steps 3–5).

Validate (ffprobe, limits) -> normalise (ffmpeg) -> silence check -> store outputs at deterministic keys.
Input errors (corrupt, too long, silent) end the recording as failed(code) without retries: the job itself succeeds,
because there is nothing to retry. Timeouts are transient and retried by the job policy (doc 01 §6).
Step 6: allocate the recording's transcript revision and enqueue `stt.batch` (T1.2).
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.retry import InputError
from app.core.storage import ObjectStore
from app.modules.audio import media
from app.modules.audio.service import object_prefix
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.stt.job import enqueue_stt
from app.worker.runner import Handler, JobContext


async def _progress(engine: AsyncEngine, session_id: uuid.UUID, stream_id: uuid.UUID, status: str,
                    progress: float) -> None:
    """Intermediate stage, committed on its own so the UI sees progress while the job runs."""
    async with engine.begin() as c:
        moved = (await c.execute(text(  # never overwrites 'failed' (e.g. consent revoked, KI-1)
            "UPDATE audio_streams SET status = :st WHERE id = :id AND deleted_at IS NULL AND status <> 'failed'"),
            {"st": status, "id": stream_id})).rowcount
        if moved:
            await publish(c, session_id, EventType.RECORDING_STATUS,
                          {"recording_id": str(stream_id), "stage": status, "progress": progress})


async def _fail(ctx: JobContext, row: Any, code: str, warnings: list[str]) -> None:
    await ctx.conn.execute(text(
        "UPDATE audio_streams SET status = 'failed', status_code = :c, warnings = CAST(:w AS jsonb) WHERE id = :id"),
        {"c": code, "w": json.dumps(warnings), "id": row.id})
    await ctx.conn.execute(text(
        "UPDATE sessions SET state = 'ready', state_reason = :c WHERE id = :s AND state = 'processing'"),
        {"c": f"recording_{code}", "s": row.session_id})
    await publish(ctx.conn, row.session_id, EventType.RECORDING_STATUS,
                  {"recording_id": str(row.id), "stage": "failed", "progress": 1.0, "code": code})


def make_ingest_handler(store: ObjectStore) -> Handler:
    async def handle(ctx: JobContext) -> None:
        s = get_settings()
        stream_id = uuid.UUID(ctx.job.payload["audio_stream_id"])
        row = (await ctx.conn.execute(text(
            "SELECT id, session_id, channel_layout, object_key_original, warnings, deleted_at FROM audio_streams "
            "WHERE id = :id"), {"id": stream_id})).first()
        if row is None or row.deleted_at is not None:
            return  # replaced or erased before ingest ran: nothing to do
        warnings = [w for w in (row.warnings or []) if w.startswith("recorded_on:")]
        prefix = object_prefix(row.session_id, row.id)
        await _progress(ctx.engine, row.session_id, row.id, "validating", 0.1)
        with tempfile.TemporaryDirectory(prefix="ss-ingest-") as d:
            work = Path(d)
            src = work / "original"
            await store.get_file("audio", row.object_key_original, str(src))
            try:
                p = await media.probe(src, s.FFPROBE_TIMEOUT_S)
                if p.duration_ms is not None and p.duration_ms > s.MAX_AUDIO_MINUTES * 60_000:
                    raise InputError("recording too long", "too_long")
                layout = row.channel_layout
                if layout == "stereo_split" and p.channels != 2:
                    warnings.append("stereo_split_requested_but_not_stereo")
                    layout = "mono"
                await ctx.checkpoint()
                await _progress(ctx.engine, row.session_id, row.id, "normalising", 0.4)
                outs = await media.normalise(src, work, stereo_split=layout == "stereo_split",
                                             timeout_s=s.FFMPEG_TIMEOUT_S)
                duration_ms = (await media.probe(outs["normalized.flac"], s.FFPROBE_TIMEOUT_S)).duration_ms
                if not duration_ms:
                    raise InputError("empty audio", "unsupported_media")
                if duration_ms > s.MAX_AUDIO_MINUTES * 60_000:
                    raise InputError("recording too long", "too_long")
                silent = await media.silent_fraction(outs["normalized.flac"], duration_ms, s.FFMPEG_TIMEOUT_S)
                if silent > media.SILENT_FRACTION:
                    raise InputError("no speech detected", "no_speech_detected")
            except InputError as exc:
                await _fail(ctx, row, exc.code, warnings)
                return
            await ctx.checkpoint()
            for name, path in outs.items():
                ctype = "audio/flac" if name.endswith(".flac") else "audio/wav"
                await store.put_file("audio", f"{prefix}/{name}", str(path), ctype)
        await ctx.checkpoint()
        done = (await ctx.conn.execute(text(
            "UPDATE audio_streams SET status = 'normalised', status_code = NULL, duration_ms = :d, "
            "channel_layout = :cl, object_key_normalized = :k, warnings = CAST(:w AS jsonb) "
            "WHERE id = :id AND status <> 'failed'"),
            {"d": duration_ms, "cl": layout, "k": f"{prefix}/normalized.wav", "w": json.dumps(warnings),
             "id": row.id})).rowcount
        if not done:
            return  # failed meanwhile (e.g. consent revoked): do not enqueue transcription
        await publish(ctx.conn, row.session_id, EventType.RECORDING_STATUS,
                      {"recording_id": str(row.id), "stage": "normalising", "progress": 1.0})
        await enqueue_stt(ctx.conn, row.session_id, row.id)  # doc 02 §5 step 6

    return handle
