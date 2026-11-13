"""Fake STT provider for tests. Not a transcription implementation."""

from __future__ import annotations

from app.modules.stt.base import TranscriptSegment


class FakeSTTProvider:
    name = "fake-stt"

    def __init__(self, segments: list[TranscriptSegment] | None = None) -> None:
        self.segments = segments or []

    async def transcribe_file(self, object_key: str, *, diarize: bool, num_speakers: int | None,
                              language_hint: str | None, keyterms: list[str]) -> list[TranscriptSegment]:
        return list(self.segments)
