"""ElevenLabs Scribe v2 batch STT adapter (doc 03 §6; T1.2).

POST {base}/v1/speech-to-text (multipart): model_id, file, diarize, num_speakers, timestamps_granularity=word,
keyterms (repeated field), language_code only when forced. Response words carry text/type/start/end/speaker_id/logprob;
`spacing` and `audio_event` entries are dropped. Errors follow doc 03 §9: 401/403 -> input `provider_auth`
(no retry); 429/5xx/timeouts -> transient (in-call retries, then the job retry policy); other 4xx -> input.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.retry import InputError, call_with_retries
from app.core.storage import ObjectStore
from app.modules.stt.base import TranscriptSegment
from app.modules.stt.segmentation import RawWord, build_segments, logprob_to_confidence

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
# Provider limits (API reference): each keyterm ≤ 50 chars and ≤ 5 words; unsupported characters rejected.
_KEYTERM_BAD = set("<>{}[]\\")


def clean_keyterms(terms: list[str], limit: int = 50) -> list[str]:
    out: list[str] = []
    for t in terms:
        t = " ".join(t.split())
        if t and len(t) <= 50 and len(t.split()) <= 5 and not (_KEYTERM_BAD & set(t)) and t not in out:
            out.append(t)
    return out[:limit]


def parse_words(payload: dict[str, Any]) -> tuple[list[RawWord], str | None]:
    words: list[RawWord] = []
    for w in payload.get("words") or []:
        if w.get("type") != "word" or w.get("start") is None or w.get("end") is None:
            continue
        text = (w.get("text") or "").strip()
        if not text:
            continue
        words.append(RawWord(text=text, start_ms=round(float(w["start"]) * 1000), end_ms=round(float(w["end"]) * 1000),
                             confidence=logprob_to_confidence(w.get("logprob")), speaker_label=w.get("speaker_id")))
    return words, payload.get("language_code")


class ElevenLabsSTT:
    name = "elevenlabs"

    def __init__(self, store: ObjectStore, *, api_key: str, model_id: str = "scribe_v2",
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = 600.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._store = store
        self._key = api_key
        self.model_id = model_id
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport  # tests inject recorded responses (no network in CI, doc 03 §10)

    def config(self, *, diarize: bool, num_speakers: int | None, keyterms: list[str]) -> dict[str, Any]:
        """Provider config stored on the transcript revision (doc 11 transcript_revisions.provider_config)."""
        return {"model_id": self.model_id, "diarize": diarize, "num_speakers": num_speakers,
                "timestamps_granularity": "word", "keyterms": clean_keyterms(keyterms)}

    async def _post(self, path: Path, form: dict[str, str | list[str]]) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout, transport=self._transport) as c:
            with path.open("rb") as f:
                r = await c.post("/v1/speech-to-text", headers={"xi-api-key": self._key}, data=form,
                                 files={"file": ("audio.wav", f, "audio/wav")})
        if r.status_code in (401, 403):
            raise InputError("ElevenLabs rejected the API key", "provider_auth")
        r.raise_for_status()  # 429/5xx -> transient; other 4xx -> input (app.core.retry.classify)
        return r.json()  # type: ignore[no-any-return]

    async def transcribe_file(self, object_key: str, *, diarize: bool, num_speakers: int | None,
                              language_hint: str | None, keyterms: list[str], session_id: uuid.UUID,
                              transcript_revision: int, channel: Literal["mono", "L", "R"]) -> list[TranscriptSegment]:
        if not self._key:
            raise InputError("ELEVENLABS_API_KEY is not configured", "provider_auth")
        form: dict[str, str | list[str]] = {"model_id": self.model_id, "diarize": "true" if diarize else "false",
                                            "timestamps_granularity": "word", "tag_audio_events": "false"}
        if diarize and num_speakers:
            form["num_speakers"] = str(num_speakers)
        if language_hint:  # only when the user forced a language (doc 03 §6)
            form["language_code"] = language_hint
        terms = clean_keyterms(keyterms)
        if terms:
            form["keyterms"] = terms  # repeated multipart field
        with tempfile.TemporaryDirectory(prefix="ss-stt-") as d:
            path = Path(d) / "audio.wav"
            await self._store.get_file("audio", object_key, str(path))
            payload = await call_with_retries(lambda: self._post(path, form))
        words, language = parse_words(payload)
        if channel != "mono":  # stereo split: the channel is the speaker (doc 03 §5, speaker_source=channel)
            for w in words:
                w.speaker_label = channel
        return build_segments(words, session_id=session_id, transcript_revision=transcript_revision,
                              channel=channel, language=language)
