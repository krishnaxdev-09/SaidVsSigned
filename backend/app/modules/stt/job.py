"""Worker job `stt.batch:{audio_stream_id}:{transcript_revision}` (doc 03 §6; T1.2).

1. Transcribe normalized.wav (mono, diarized) or L.wav + R.wav (stereo split, one speaker per side).
2. Segment (shared code), compute is_numeric and ASR confidences.
3. Persist atomically: the revision's segments/words are deleted and rewritten in the job transaction, the revision
   row is filled in and made current. Re-running the same job therefore replaces rather than duplicates.
4. Recording -> ready; emit `transcript.ready`.
Step 5: enqueue the auto role suggestion `speakers.suggest` (T1.3). Enqueueing `claims.extract` is T1.6.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.retry import InputError
from app.modules.audio.service import object_prefix
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.speakers.job import enqueue_suggest
from app.modules.stt.base import STTProvider, TranscriptSegment
from app.worker import queue
from app.worker.runner import Handler, JobContext

# doc 03 §6 keyterm list; the session's lender name is added in front.
BASE_KEYTERMS = ["processing fee", "foreclosure", "prepayment", "part payment", "KFS", "APR", "EMI", "flat rate",
                 "reducing balance", "GST", "insurance", "lakh", "crore", "penal charges"]


async def allocate_revision(conn: AsyncConnection, session_id: uuid.UUID, stream_id: uuid.UUID) -> int:
    """One revision per recording: reuse the stream's revision if it exists, otherwise take max + 1.
    The row (provider NULL) marks the revision as allocated but not yet transcribed."""
    await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": session_id})
    existing = (await conn.execute(text(
        "SELECT revision FROM transcript_revisions WHERE session_id = :s AND audio_stream_id = :a"),
        {"s": session_id, "a": stream_id})).scalar()
    if existing is not None:
        return int(existing)
    rev = int((await conn.execute(text(
        "SELECT coalesce(max(revision), 0) + 1 FROM transcript_revisions WHERE session_id = :s"),
        {"s": session_id})).scalar_one())
    await conn.execute(text(
        "INSERT INTO transcript_revisions (session_id, revision, audio_stream_id, is_current) VALUES (:s, :r, :a, false)"),
        {"s": session_id, "r": rev, "a": stream_id})
    return rev


async def enqueue_stt(conn: AsyncConnection, session_id: uuid.UUID, stream_id: uuid.UUID) -> int:
    rev = await allocate_revision(conn, session_id, stream_id)
    await queue.enqueue(conn, "stt.batch", {"audio_stream_id": str(stream_id), "transcript_revision": rev},
                        session_id=session_id, dedupe_key=f"stt.batch:{stream_id}:{rev}")
    return rev


async def _status(ctx: JobContext, session_id: uuid.UUID, stream_id: uuid.UUID, status: str, progress: float) -> None:
    async with ctx.engine.begin() as c:  # committed on its own so the UI sees progress during the provider call
        await c.execute(text("UPDATE audio_streams SET status = :st WHERE id = :id AND deleted_at IS NULL"),
                        {"st": status, "id": stream_id})
        await publish(c, session_id, EventType.RECORDING_STATUS,
                      {"recording_id": str(stream_id), "stage": status, "progress": progress})


async def _fail(ctx: JobContext, session_id: uuid.UUID, stream_id: uuid.UUID, code: str) -> None:
    await ctx.conn.execute(text("UPDATE audio_streams SET status = 'failed', status_code = :c WHERE id = :id"),
                           {"c": code, "id": stream_id})
    await ctx.conn.execute(text(
        "UPDATE sessions SET state = 'ready', state_reason = :c WHERE id = :s AND state = 'processing'"),
        {"c": f"recording_{code}", "s": session_id})
    await publish(ctx.conn, session_id, EventType.RECORDING_STATUS,
                  {"recording_id": str(stream_id), "stage": "failed", "progress": 1.0, "code": code})


async def _persist(conn: AsyncConnection, session_id: uuid.UUID, rev: int, segments: list[TranscriptSegment],
                   provider: str, config: dict[str, Any]) -> None:
    await conn.execute(text("DELETE FROM transcript_segments WHERE session_id = :s AND transcript_revision = :r"),
                       {"s": session_id, "r": rev})  # words cascade
    if segments:
        await conn.execute(text(
            "INSERT INTO transcript_segments (id, session_id, transcript_revision, channel, speaker_label, text, "
            "start_ms, end_ms, language, asr_avg_conf, asr_min_numeric_conf) "
            "VALUES (:id, :s, :r, :ch, :sp, :t, :a, :b, :lang, :avg, :minn)"),
            [{"id": g.id, "s": session_id, "r": rev, "ch": g.channel, "sp": g.speaker_label, "t": g.text,
              "a": g.start_ms, "b": g.end_ms, "lang": g.language, "avg": g.asr_avg_conf,
              "minn": g.asr_min_numeric_conf} for g in segments])
        words = [{"seg": g.id, "i": i, "t": w.text, "a": w.start_ms, "b": w.end_ms, "c": w.confidence,
                  "sp": w.speaker_label, "n": w.is_numeric} for g in segments for i, w in enumerate(g.words)]
        await conn.execute(text(
            "INSERT INTO transcript_words (segment_id, idx, text, start_ms, end_ms, confidence, speaker_label, "
            "is_numeric) VALUES (:seg, :i, :t, :a, :b, :c, :sp, :n)"), words)
    await conn.execute(text("UPDATE transcript_revisions SET is_current = false WHERE session_id = :s AND revision <> :r"),
                       {"s": session_id, "r": rev})
    await conn.execute(text(
        "UPDATE transcript_revisions SET provider = :p, provider_config = CAST(:c AS jsonb), is_current = true "
        "WHERE session_id = :s AND revision = :r"),
        {"p": provider, "c": json.dumps(config), "s": session_id, "r": rev})


def make_stt_handler(stt: STTProvider) -> Handler:
    async def handle(ctx: JobContext) -> None:
        s = get_settings()
        stream_id = uuid.UUID(ctx.job.payload["audio_stream_id"])
        rev = int(ctx.job.payload["transcript_revision"])
        row = (await ctx.conn.execute(text(
            "SELECT a.session_id, a.channel_layout, a.deleted_at, a.status, s.lender_name_raw FROM audio_streams a "
            "JOIN sessions s ON s.id = a.session_id WHERE a.id = :id"), {"id": stream_id})).first()
        if row is None or row.deleted_at is not None or row.status == "failed":
            return  # replaced, erased or failed before transcription: nothing to do
        session_id = row.session_id
        prefix = object_prefix(session_id, stream_id)
        keyterms = ([row.lender_name_raw] if row.lender_name_raw else []) + BASE_KEYTERMS
        diarize = row.channel_layout != "stereo_split"
        await _status(ctx, session_id, stream_id, "transcribing", 0.5)
        await ctx.checkpoint()  # before the paid provider call (doc 01 §5.4)
        sources = ([("L", f"{prefix}/L.wav"), ("R", f"{prefix}/R.wav")] if not diarize
                   else [("mono", f"{prefix}/normalized.wav")])
        segments: list[TranscriptSegment] = []
        try:
            for channel, key in sources:
                segments += await stt.transcribe_file(
                    key, diarize=diarize, num_speakers=s.STT_NUM_SPEAKERS if diarize else None, language_hint=None,
                    keyterms=keyterms, session_id=session_id, transcript_revision=rev,
                    channel=channel)  # type: ignore[arg-type]
                await ctx.checkpoint()
            if not segments:
                raise InputError("empty transcript", "no_speech_detected")
        except InputError as exc:
            await _fail(ctx, session_id, stream_id, exc.code)
            return
        segments.sort(key=lambda g: (g.start_ms, g.channel))
        config = stt.config(diarize=diarize, num_speakers=s.STT_NUM_SPEAKERS if diarize else None,
                            keyterms=keyterms) if hasattr(stt, "config") else {}
        await _persist(ctx.conn, session_id, rev, segments, stt.name, config)
        await ctx.conn.execute(text("UPDATE audio_streams SET status = 'ready', status_code = NULL WHERE id = :id"),
                               {"id": stream_id})
        await publish(ctx.conn, session_id, EventType.RECORDING_STATUS,
                      {"recording_id": str(stream_id), "stage": "ready", "progress": 1.0})
        await publish(ctx.conn, session_id, EventType.TRANSCRIPT_READY,
                      {"transcript_revision": rev, "segment_count": len(segments)})
        await enqueue_suggest(ctx.conn, session_id, rev)  # doc 03 §6 step 5

    return handle
