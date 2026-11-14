"""STTProvider protocol + transcript models as doc 03 §4 (canonical there). `stream()` is deferred to Later-L1.

T1.2 note: `transcribe_file` also takes `session_id`, `transcript_revision` and `channel`, because every returned
TranscriptSegment carries them and the doc 03 §4 signature has no other way to supply them. The models are unchanged."""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

from pydantic import BaseModel


class WordToken(BaseModel):
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    speaker_label: str | None
    is_numeric: bool


class TranscriptSegment(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    transcript_revision: int
    channel: Literal["mono", "L", "R"]
    speaker_label: str | None
    text: str
    start_ms: int
    end_ms: int
    words: list[WordToken]
    language: str | None
    asr_avg_conf: float | None
    asr_min_numeric_conf: float | None


class STTProvider(Protocol):
    name: str

    async def transcribe_file(self, object_key: str, *, diarize: bool, num_speakers: int | None,
                              language_hint: str | None, keyterms: list[str], session_id: uuid.UUID,
                              transcript_revision: int, channel: Literal["mono", "L", "R"]) -> list[TranscriptSegment]: ...
