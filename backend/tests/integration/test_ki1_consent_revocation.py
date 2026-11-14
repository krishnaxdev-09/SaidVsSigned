"""KI-1 regression: withdrawing consent while a recording is queued or being processed (doc 01 §5.4).

The recording must end as failed(consent_revoked); no running or late worker may overwrite that; no transcript,
speaker suggestion or other downstream result may be saved."""

from __future__ import annotations

import uuid

import pytest

from app.modules.audio.ingest import _progress
from app.modules.stt.fakes import FakeSTTProvider
from app.modules.stt.segmentation import RawWord
from tests.conftest import FakeStore, scalar
from tests.integration.test_transcripts import drain, ready_session, recorded_elevenlabs, upload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

WORDS = [RawWord("Fee", 0, 300, 0.9, "speaker_0"), RawWord("Okay", 900, 1200, 0.9, "speaker_1")]


async def revoke(client, h, sid):  # type: ignore[no-untyped-def]
    r = await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def recording(client, h, rid):  # type: ignore[no-untyped-def]
    return (await client.get(f"/api/v1/recordings/{rid}", headers=h)).json()


async def nothing_downstream(engine, sid: str) -> None:  # type: ignore[no-untyped-def]
    s = uuid.UUID(sid)
    for sql in ("SELECT count(*) FROM transcript_segments WHERE session_id = :s",
                "SELECT count(*) FROM speaker_assignments WHERE session_id = :s",
                "SELECT count(*) FROM events WHERE session_id = :s AND type IN ('transcript.ready', 'speaker.updated')",
                "SELECT count(*) FROM transcript_revisions WHERE session_id = :s AND provider IS NOT NULL"):
        assert await scalar(engine, sql, s=s) == 0, sql


async def test_revoked_while_queued(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    out = await revoke(client, h, sid)
    assert out["jobs_cancelled"] == 1 and out["session"]["state_reason"] == "consent_revoked"
    rec = await recording(client, h, rid)
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")
    await drain(engine, fake_store, FakeSTTProvider(WORDS))
    rec = await recording(client, h, rid)
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")
    await nothing_downstream(engine, sid)
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'recording.status' "
                                "AND payload->>'code' = 'consent_revoked'", s=uuid.UUID(sid)) == 1


async def test_revoked_during_transcription(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])

    class RevokingSTT(FakeSTTProvider):
        async def transcribe_file(self, object_key, **kw):  # type: ignore[no-untyped-def,override]
            assert (await recording(client, h, rid))["status"] == "transcribing"
            assert (await revoke(client, h, sid))["jobs_cancel_requested"] == 1
            return await super().transcribe_file(object_key, **kw)

    await drain(engine, fake_store, RevokingSTT(WORDS))
    rec = await recording(client, h, rid)
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")  # was stuck at 'transcribing' (KI-1)
    await nothing_downstream(engine, sid)
    assert await scalar(engine, "SELECT status FROM jobs WHERE kind = 'stt.batch' AND session_id = :s",
                        s=uuid.UUID(sid)) == "cancelled"


async def test_revoked_during_ingest(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])

    class RevokingStore(FakeStore):
        async def get_file(self, bucket, key, path):  # type: ignore[no-untyped-def]
            if key.endswith("/original"):
                await revoke(client, h, sid)
            return await fake_store.get_file(bucket, key, path)

        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            return getattr(fake_store, name)

    store = RevokingStore()
    store.objects = fake_store.objects
    await drain(engine, store, FakeSTTProvider(WORDS))
    rec = await recording(client, h, rid)
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")
    assert ("audio", f"{sid}/{rid}/normalized.wav") not in fake_store.objects
    assert await scalar(engine, "SELECT count(*) FROM jobs WHERE kind = 'stt.batch' AND session_id = :s",
                        s=uuid.UUID(sid)) == 0
    await nothing_downstream(engine, sid)


async def test_late_progress_write_cannot_overwrite(client, engine, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    await revoke(client, h, sid)
    await _progress(engine, uuid.UUID(sid), uuid.UUID(rid), "normalising", 0.4)  # a worker that missed the revocation
    rec = await recording(client, h, rid)
    assert (rec["status"], rec["status_code"]) == ("failed", "consent_revoked")


async def test_finished_recording_is_left_for_erasure(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, tone_files["a"])
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0])
    assert (await recording(client, h, rid))["status"] == "ready"
    await revoke(client, h, sid)
    assert (await recording(client, h, rid))["status"] == "ready"  # finished; deletion is session erase (T4.3)
