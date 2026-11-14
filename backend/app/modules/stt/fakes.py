"""Fake STT provider for automated tests only. Not a transcription implementation."""

from __future__ import annotations

import uuid
from typing import Literal

from app.modules.stt.base import TranscriptSegment
from app.modules.stt.segmentation import RawWord, build_segments


class FakeSTTProvider:
    """Returns scripted words (per channel) run through the real segmentation code."""

    name = "fake-stt"

    def __init__(self, words: list[RawWord] | dict[str, list[RawWord]] | None = None,
                 language: str | None = "en", error: BaseException | None = None) -> None:
        self.words = words or []
        self.language = language
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def transcribe_file(self, object_key: str, *, diarize: bool, num_speakers: int | None,
                              language_hint: str | None, keyterms: list[str], session_id: uuid.UUID,
                              transcript_revision: int, channel: Literal["mono", "L", "R"]) -> list[TranscriptSegment]:
        self.calls.append({"object_key": object_key, "diarize": diarize, "num_speakers": num_speakers,
                           "keyterms": keyterms, "channel": channel})
        if self.error is not None:
            raise self.error
        words = self.words.get(channel, []) if isinstance(self.words, dict) else self.words
        return build_segments(list(words), session_id=session_id, transcript_revision=transcript_revision,
                              channel=channel, language=self.language)
