"""T1.2 batch STT + transcript revisions (doc 03 §6, §9–§11): upload → ingest → stt.batch → segments/words → API.

The ElevenLabs adapter runs against a recorded response (no network in CI). The FakeSTTProvider runs the same flow
(provider independence, doc 03 §11)."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.core.retry import InputError
from app.modules.stt.elevenlabs import ElevenLabsSTT
from app.modules.stt.fakes import FakeSTTProvider
from app.modules.stt.segmentation import RawWord
from app.worker.handlers import register_all
from app.worker.runner import Registry, Worker
from tests.conftest import create_session, give_consent, register_and_login, scalar

if shutil.which("ffmpeg") is None and os.environ.get("REQUIRE_DB") == "1":
    raise RuntimeError("ffmpeg/ffprobe are required for the transcript tests (CI and `make ci`)")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session"),
              pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")]

CASSETTE = json.loads((Path(__file__).parents[1] / "fixtures" / "elevenlabs" / "ag07_two_speaker_en.json").read_text())


def recorded_elevenlabs(store, status: int = 200):  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(status, json=CASSETTE if status == 200 else {"detail": "x"})

    return ElevenLabsSTT(store, api_key="test-key", transport=httpx.MockTransport(handler)), calls


class OfflineLLM:
    """Default for these tests: no LLM available, so speaker suggestion uses its heuristic fallback (no network)."""

    name = "offline"

    async def structured(self, **kw):  # type: ignore[no-untyped-def]
        raise InputError("no LLM in tests", "provider_auth")


async def drain(engine, store, stt, llm=None) -> int:  # type: ignore[no-untyped-def]
    reg = Registry()
    register_all(reg, store, stt=stt, llm=llm or OfflineLLM())
    worker = Worker(engine, reg, lease_seconds=60)
    n = 0
    while await worker.run_once():
        n += 1
    return n


async def ready_session(client, **over):  # type: ignore[no-untyped-def]
    _, h = await register_and_login(client)
    sid = (await create_session(client, h, **over))["id"]
    assert (await give_consent(client, h, sid)).status_code == 201
    return h, sid


async def upload(client, h, sid, path: Path, **form: str) -> str:  # type: ignore[no-untyped-def]
    r = await client.post(f"/api/v1/sessions/{sid}/recordings", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                          files={"file": (path.name, path.read_bytes())}, data=form)
    assert r.status_code == 202, r.text
    return r.json()["recording_id"]


async def test_transcript_from_recorded_elevenlabs_response(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client, lender_name="Udyam Capital")
    assert (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).status_code == 404
    rid = await upload(client, h, sid, tone_files["a"])
    stt, calls = recorded_elevenlabs(fake_store)
    await drain(engine, fake_store, stt)
    # Other tests may leave stt jobs queued, so check this session's request rather than counting calls.
    assert any(b"Udyam Capital" in c.read() for c in calls)  # lender name sent as a keyterm
    rec = (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()
    assert rec["status"] == "ready" and rec["stage"] == "ready"
    t = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()
    assert t["transcript_revision"] == 1 and t["is_current"] and t["provider"] == "elevenlabs"
    assert t["audio_stream_id"] == rid
    segs = t["segments"]
    assert [g["speaker_label"] for g in segs] == ["speaker_0", "speaker_1", "speaker_0", "speaker_1"]
    assert all(g["channel"] == "mono" and g["words"] and g["asr_avg_conf"] for g in segs)
    words = [w for g in segs for w in g["words"]]
    assert all(w["start_ms"] <= w["end_ms"] for w in words) and any(w["is_numeric"] for w in words)
    s = uuid.UUID(sid)
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'transcript.ready'", s=s) == 1
    cfg = await scalar(engine, "SELECT provider_config FROM transcript_revisions WHERE session_id = :s", s=s)
    assert cfg["model_id"] == "scribe_v2" and cfg["diarize"] is True and cfg["num_speakers"] == 2


async def test_rerun_rewrites_the_revision_atomically(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    stt, _ = recorded_elevenlabs(fake_store)
    await drain(engine, fake_store, stt)
    s = uuid.UUID(sid)
    before = await scalar(engine, "SELECT count(*) FROM transcript_words w JOIN transcript_segments g ON g.id = w.segment_id "
                                  "WHERE g.session_id = :s", s=s)
    async with engine.begin() as conn:
        from app.worker import queue

        await queue.enqueue(conn, "stt.batch", {"audio_stream_id": rid, "transcript_revision": 1}, session_id=s,
                            dedupe_key=f"stt.batch:{rid}:1")
    await drain(engine, fake_store, stt)
    assert await scalar(engine, "SELECT count(*) FROM transcript_segments WHERE session_id = :s", s=s) == 4
    assert await scalar(engine, "SELECT count(*) FROM transcript_words w JOIN transcript_segments g ON g.id = w.segment_id "
                                "WHERE g.session_id = :s", s=s) == before
    assert await scalar(engine, "SELECT count(*) FROM transcript_revisions WHERE session_id = :s", s=s) == 1


async def test_replace_creates_new_current_revision(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    old = await upload(client, h, sid, tone_files["a"])
    stt, _ = recorded_elevenlabs(fake_store)
    await drain(engine, fake_store, stt)
    put = await client.put(f"/api/v1/recordings/{old}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                           files={"file": ("b.wav", tone_files["b"].read_bytes())})
    assert put.status_code == 202
    await drain(engine, fake_store, stt)
    cur = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()
    assert cur["transcript_revision"] == 2 and cur["is_current"] and cur["audio_stream_id"] == put.json()["recording_id"]
    prev = (await client.get(f"/api/v1/sessions/{sid}/transcript", params={"revision": 1}, headers=h)).json()
    assert prev["transcript_revision"] == 1 and prev["is_current"] is False and prev["audio_stream_id"] == old
    assert prev["segments"]  # old revision kept for audit (doc 04 §8)


async def test_fake_provider_same_flow(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    fake = FakeSTTProvider([RawWord("Fee", 0, 300, 0.9, "speaker_0"), RawWord("2%", 350, 600, 0.95, "speaker_0")])
    await drain(engine, fake_store, fake)
    assert (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()["status"] == "ready"
    t = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()
    assert t["provider"] == "fake-stt" and t["segments"][0]["text"] == "Fee 2%"
    assert fake.calls[0]["diarize"] is True and fake.calls[0]["num_speakers"] == 2


async def test_stereo_split_transcribes_each_side(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    await upload(client, h, sid, tone_files["stereo"], channel_layout="stereo_split")
    fake = FakeSTTProvider({"L": [RawWord("Fee", 0, 300, 0.9, None)], "R": [RawWord("Okay", 500, 800, 0.9, None)]})
    await drain(engine, fake_store, fake)
    assert sorted(c["channel"] for c in fake.calls) == ["L", "R"] and all(not c["diarize"] for c in fake.calls)
    segs = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()["segments"]
    assert [(g["channel"], g["text"]) for g in segs] == [("L", "Fee"), ("R", "Okay")]


async def test_empty_transcript_fails_without_retry(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    await drain(engine, fake_store, FakeSTTProvider([]))
    rec = (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()
    assert rec["status"] == "failed" and rec["status_code"] == "no_speech_detected"
    assert await _attempts(engine, rid) == 1


async def test_auth_failure_is_reported_not_retried(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    stt, calls = recorded_elevenlabs(fake_store, status=401)
    await drain(engine, fake_store, stt)
    rec = (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()
    assert rec["status_code"] == "provider_auth" and calls and await _attempts(engine, rid) == 1
    assert (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).status_code == 404


async def test_transcript_is_owner_only(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    await upload(client, h, sid, tone_files["a"])
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0])
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=other)).status_code == 404


async def _attempts(engine, rid: str) -> int:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, attempts FROM jobs WHERE kind = 'stt.batch' AND payload->>'audio_stream_id' = :r"),
            {"r": rid})).first()
    assert row is not None and row.status == "succeeded"
    return int(row.attempts)


async def test_revocation_during_transcription_commits_nothing(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    """doc 01 §5.4: revocation while the provider call is in flight -> the result is discarded, job cancelled."""
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])

    class RevokingSTT(FakeSTTProvider):
        async def transcribe_file(self, object_key, **kw):  # type: ignore[no-untyped-def,override]
            r = await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)
            assert r.status_code == 200 and r.json()["jobs_cancel_requested"] == 1
            return await super().transcribe_file(object_key, **kw)

    await drain(engine, fake_store, RevokingSTT([RawWord("Fee", 0, 300, 0.9, "speaker_0")]))
    s = uuid.UUID(sid)
    assert await scalar(engine, "SELECT status FROM jobs WHERE kind = 'stt.batch' AND session_id = :s", s=s) == "cancelled"
    assert await scalar(engine, "SELECT count(*) FROM transcript_segments WHERE session_id = :s", s=s) == 0
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'transcript.ready'", s=s) == 0
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'job.cancel_observed' "
                                "AND meta->>'session_id' = :s", s=sid) == 1
    rec = (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")  # KI-1
