"""ElevenLabs adapter contract tests against a recorded response (doc 03 §10: cassettes, no network in CI).

Cassette: tests/fixtures/elevenlabs/ag07_two_speaker_en.json, recorded 2026-11-14 from scribe_v2 on a synthetic
two-voice call (agent / customer), turn starts 0.00 / 6.95 / 10.06 / 18.52 s.
"""

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.core import retry
from app.core.retry import InputError
from app.modules.stt.elevenlabs import ElevenLabsSTT, clean_keyterms
from tests.conftest import FakeStore

CASSETTE = json.loads((Path(__file__).parents[1] / "fixtures" / "elevenlabs" / "ag07_two_speaker_en.json").read_text())
TRUE_TURN_STARTS_S = [0.0, 6.951, 10.059, 18.524]


def _store() -> FakeStore:
    st = FakeStore()
    st.objects[("audio", "s/r/normalized.wav")] = b"RIFF0000WAVEfake"
    return st


def _stt(handler, key: str = "test-key") -> ElevenLabsSTT:  # type: ignore[no-untyped-def]
    return ElevenLabsSTT(_store(), api_key=key, transport=httpx.MockTransport(handler))


def _run(stt: ElevenLabsSTT, **kw):  # type: ignore[no-untyped-def]
    args = {"diarize": True, "num_speakers": 2, "language_hint": None, "keyterms": ["Udyam Capital", "EMI"],
            "session_id": uuid.uuid4(), "transcript_revision": 1, "channel": "mono", **kw}
    return asyncio.run(stt.transcribe_file("s/r/normalized.wav", **args))


def test_request_and_parsed_segments() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers["xi-api-key"], req.read()
        return httpx.Response(200, json=CASSETTE)

    segs = _run(_stt(handler))
    body = seen["body"]
    assert seen["url"] == "https://api.elevenlabs.io/v1/speech-to-text" and seen["key"] == "test-key"
    for field in (b'name="model_id"\r\n\r\nscribe_v2', b'name="diarize"\r\n\r\ntrue', b'name="num_speakers"\r\n\r\n2',
                  b'name="timestamps_granularity"\r\n\r\nword', b'name="keyterms"\r\n\r\nUdyam Capital',
                  b'name="keyterms"\r\n\r\nEMI'):
        assert field in body  # type: ignore[operator]
    assert b'name="language_code"' not in body  # type: ignore[operator]
    assert [g.speaker_label for g in segs] == ["speaker_0", "speaker_1", "speaker_0", "speaker_1"]
    assert segs[2].text.startswith("The processing fee is only 1%")
    for g, true_s in zip(segs, TRUE_TURN_STARTS_S, strict=True):
        assert abs(g.start_ms / 1000 - true_s) <= 0.2  # word timestamps within ±200 ms (doc 03 §11)
    numeric = {w.text for g in segs for w in g.words if w.is_numeric}
    assert {"10", "lakh", "1%,", "36,000"} <= numeric
    assert all(w.confidence is not None and 0 < w.confidence <= 1 for g in segs for w in g.words)
    assert all(g.language == CASSETTE["language_code"] for g in segs)


def test_stereo_channel_is_the_speaker() -> None:
    segs = _run(_stt(lambda r: httpx.Response(200, json=CASSETTE)), diarize=False, num_speakers=None, channel="L")
    assert {g.speaker_label for g in segs} == {"L"} and {g.channel for g in segs} == {"L"}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_input_errors(status: int) -> None:
    with pytest.raises(InputError) as e:
        _run(_stt(lambda r: httpx.Response(status, json={"detail": "bad key"})))
    assert e.value.code == "provider_auth"


def test_missing_key_is_provider_auth() -> None:
    with pytest.raises(InputError) as e:
        _run(_stt(lambda r: httpx.Response(200, json=CASSETTE), key=""))
    assert e.value.code == "provider_auth"


def test_transient_errors_retry_in_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry, "IN_CALL_BACKOFF", (0.0, 0.0))
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] == 1 else 200, json=CASSETTE)

    assert len(_run(_stt(handler))) == 4 and calls["n"] == 2

    def always_500(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError) as e:
        _run(_stt(always_500))
    assert retry.classify(e.value) is retry.ErrorClass.TRANSIENT


def test_keyterm_limits() -> None:
    terms = clean_keyterms(["ok", "a" * 51, "one two three four five six", "bad<", "ok", "  spaced   term "])
    assert terms == ["ok", "spaced term"]
    assert len(clean_keyterms([f"t{i}" for i in range(80)])) == 50
