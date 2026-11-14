"""Transcript endpoint (doc 11 §4: GET /sessions/{id}/transcript?revision=). Owner-only."""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import current_user
from app.core.errors import AppError
from app.modules.sessions.service import get_owned_session
from app.modules.stt.base import TranscriptSegment, WordToken

router = APIRouter(prefix="/api/v1", tags=["transcripts"])


class TranscriptOut(BaseModel):
    transcript_revision: int
    is_current: bool
    audio_stream_id: uuid.UUID | None
    provider: str | None
    segments: list[TranscriptSegment]


@router.get("/sessions/{session_id}/transcript", response_model=TranscriptOut)
async def get_transcript(session_id: uuid.UUID, revision: int | None = None,
                         user_id: uuid.UUID = Depends(current_user)) -> TranscriptOut:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        where = "revision = :r" if revision is not None else "is_current"
        rev = (await conn.execute(text(
            f"SELECT revision, is_current, audio_stream_id, provider FROM transcript_revisions "
            f"WHERE session_id = :s AND provider IS NOT NULL AND {where}"),
            {"s": session_id, "r": revision})).first()
        if rev is None:
            raise AppError("not_found", "No transcript for this session yet")
        segs = (await conn.execute(text(
            "SELECT id, channel, speaker_label, text, start_ms, end_ms, language, asr_avg_conf, asr_min_numeric_conf "
            "FROM transcript_segments WHERE session_id = :s AND transcript_revision = :r ORDER BY start_ms, channel"),
            {"s": session_id, "r": rev.revision})).all()
        words = (await conn.execute(text(
            "SELECT w.segment_id, w.text, w.start_ms, w.end_ms, w.confidence, w.speaker_label, w.is_numeric "
            "FROM transcript_words w JOIN transcript_segments g ON g.id = w.segment_id "
            "WHERE g.session_id = :s AND g.transcript_revision = :r ORDER BY w.segment_id, w.idx"),
            {"s": session_id, "r": rev.revision})).all()
    by_seg: dict[uuid.UUID, list[WordToken]] = defaultdict(list)
    for w in words:
        by_seg[w.segment_id].append(WordToken(text=w.text, start_ms=w.start_ms, end_ms=w.end_ms,
                                              confidence=w.confidence, speaker_label=w.speaker_label,
                                              is_numeric=w.is_numeric))
    return TranscriptOut(
        transcript_revision=rev.revision, is_current=bool(rev.is_current), audio_stream_id=rev.audio_stream_id,
        provider=rev.provider,
        segments=[TranscriptSegment(id=g.id, session_id=session_id, transcript_revision=rev.revision, channel=g.channel,
                                    speaker_label=g.speaker_label, text=g.text, start_ms=g.start_ms, end_ms=g.end_ms,
                                    words=by_seg[g.id], language=g.language, asr_avg_conf=g.asr_avg_conf,
                                    asr_min_numeric_conf=g.asr_min_numeric_conf) for g in segs])
