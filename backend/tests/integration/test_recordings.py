"""T1.1 recording upload pipeline (doc 02 §7–8): formats, validation, dedupe, idempotency, replace, consent gate,
silence, limits, deterministic re-runs and snippets. Audio fixtures are generated with ffmpeg at test time."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.modules.audio.ingest import make_ingest_handler
from app.worker.runner import Registry, Worker
from tests.conftest import create_session, give_consent, register_and_login, scalar

if shutil.which("ffmpeg") is None and os.environ.get("REQUIRE_DB") == "1":
    raise RuntimeError("ffmpeg/ffprobe are required for the recording tests (CI and `make ci`)")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session"),
              pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")]

TONE_S = 3.0
ENCODE = {"wav": ["-c:a", "pcm_s16le"], "mp3": ["-c:a", "libmp3lame", "-b:a", "64k"],
          "m4a": ["-c:a", "aac", "-b:a", "64k"], "aac": ["-c:a", "aac", "-b:a", "64k", "-f", "adts"],
          "webm": ["-c:a", "libopus", "-b:a", "32k"], "ogg": ["-c:a", "libopus", "-b:a", "32k"]}


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _duration_s(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         check=True, capture_output=True).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture(scope="session")
def audio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("audio")
    tone = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={TONE_S}:sample_rate=44100"]
    for ext, enc in ENCODE.items():
        _ff(*tone, "-ac", "1", *enc, str(d / f"mono.{ext}"))
    _ff("-f", "lavfi", "-i", f"sine=frequency=440:duration={TONE_S}", "-f", "lavfi", "-i",
        f"sine=frequency=880:duration={TONE_S}", "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo",
        "-c:a", "pcm_s16le", str(d / "stereo.wav"))
    _ff("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", str(TONE_S), "-c:a", "pcm_s16le", str(d / "silent.wav"))
    _ff("-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-ac", "1", "-c:a", "pcm_s16le", str(d / "other.wav"))
    (d / "corrupt.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEjunkjunkjunk" + b"\x00" * 64)
    (d / "renamed.mp3").write_bytes(b"MZ\x90\x00" + b"\x00" * 200)  # a Windows executable renamed to .mp3
    (d / "empty.wav").write_bytes(b"")
    return d


@pytest.fixture
def store(fake_store):  # type: ignore[no-untyped-def]
    return fake_store


async def _drain(engine, store) -> int:  # type: ignore[no-untyped-def]
    """Runs the T1.1 ingest job only; transcription (stt.batch) is covered by test_transcripts.py."""
    reg = Registry()
    reg.register("audio.ingest", make_ingest_handler(store))
    worker = Worker(engine, reg, lease_seconds=60)
    n = 0
    while await worker.run_once():
        n += 1
    return n


async def _ready_session(client):  # type: ignore[no-untyped-def]
    _, h = await register_and_login(client)
    sid = (await create_session(client, h))["id"]
    assert (await give_consent(client, h, sid)).status_code == 201
    return h, sid


async def _upload(client, h, sid, path: Path, key: str | None = None, **form: str):  # type: ignore[no-untyped-def]
    return await client.post(f"/api/v1/sessions/{sid}/recordings",
                             headers={**h, "Idempotency-Key": key or uuid.uuid4().hex},
                             files={"file": (path.name, path.read_bytes(), "application/octet-stream")}, data=form)


async def _recording(client, h, rid):  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/v1/recordings/{rid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_upload_requires_consent(client, audio_dir):  # type: ignore[no-untyped-def]
    _, h = await register_and_login(client)
    sid = (await create_session(client, h))["id"]
    r = await _upload(client, h, sid, audio_dir / "mono.wav")
    assert r.status_code == 403 and r.json()["code"] == "consent_required"


async def test_upload_requires_idempotency_key(client, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    r = await client.post(f"/api/v1/sessions/{sid}/recordings", headers=h,
                          files={"file": ("a.wav", (audio_dir / "mono.wav").read_bytes())})
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"


@pytest.mark.parametrize("ext", list(ENCODE))
async def test_each_format_normalises(client, engine, store, audio_dir, ext):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    src = audio_dir / f"mono.{ext}"
    r = await _upload(client, h, sid, src)
    assert r.status_code == 202, r.text
    rid = r.json()["recording_id"]
    assert r.json() == {"recording_id": rid, "status": "queued", "duplicate": False}
    assert (await client.get(f"/api/v1/sessions/{sid}", headers=h)).json()["state"] == "processing"
    await _drain(engine, store)
    rec = await _recording(client, h, rid)
    assert rec["status"] == "normalised" and rec["format"] == ext and rec["channel_layout"] == "mono", rec
    prefix = f"{sid}/{rid}"
    for name in ("original", "normalized.wav", "normalized.flac"):
        assert ("audio", f"{prefix}/{name}") in store.objects
    out = audio_dir / f"out-{ext}.flac"
    out.write_bytes(store.objects[("audio", f"{prefix}/normalized.flac")])
    # Normalised duration within ±50 ms of the source (doc 02 §7), and of the stored duration_ms.
    assert abs(_duration_s(out) - _duration_s(src)) <= 0.05
    assert abs(rec["duration_ms"] / 1000 - _duration_s(src)) <= 0.05


async def test_stereo_split_writes_both_sides(client, engine, store, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    r = await _upload(client, h, sid, audio_dir / "stereo.wav", channel_layout="stereo_split")
    rid = r.json()["recording_id"]
    await _drain(engine, store)
    rec = await _recording(client, h, rid)
    assert rec["status"] == "normalised" and rec["channel_layout"] == "stereo_split"
    assert ("audio", f"{sid}/{rid}/L.wav") in store.objects and ("audio", f"{sid}/{rid}/R.wav") in store.objects


async def test_stereo_split_on_mono_file_warns(client, engine, store, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    rid = (await _upload(client, h, sid, audio_dir / "mono.wav", channel_layout="stereo_split")).json()["recording_id"]
    await _drain(engine, store)
    rec = await _recording(client, h, rid)
    assert rec["channel_layout"] == "mono" and "stereo_split_requested_but_not_stereo" in rec["warnings"]


@pytest.mark.parametrize("name", ["renamed.mp3", "empty.wav"])
async def test_unrecognised_files_rejected_up_front(client, engine, audio_dir, name):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    r = await _upload(client, h, sid, audio_dir / name)
    assert r.status_code == 415 and r.json()["code"] == "unsupported_media"
    assert await scalar(engine, "SELECT count(*) FROM audio_streams WHERE session_id = :s", s=uuid.UUID(sid)) == 0


@pytest.mark.parametrize(("name", "code"), [("corrupt.wav", "unsupported_media"), ("silent.wav", "no_speech_detected")])
async def test_input_failures_are_not_retried(client, engine, store, audio_dir, name, code):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    r = await _upload(client, h, sid, audio_dir / name)
    assert r.status_code == 202
    rid = r.json()["recording_id"]
    await _drain(engine, store)
    rec = await _recording(client, h, rid)
    assert rec["status"] == "failed" and rec["stage"] == "failed" and rec["status_code"] == code
    job = await _job(engine, rid)
    assert job.status == "succeeded" and job.attempts == 1  # handled as an input failure: no retry
    session = (await client.get(f"/api/v1/sessions/{sid}", headers=h)).json()
    assert session["state"] == "ready" and session["state_reason"] == f"recording_{code}"
    # A failed recording can be superseded by a new upload.
    r2 = await _upload(client, h, sid, audio_dir / "mono.wav")
    assert r2.status_code == 202 and r2.json()["recording_id"] != rid


async def test_too_long_fails(client, engine, store, audio_dir, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "MAX_AUDIO_MINUTES", 0)
    h, sid = await _ready_session(client)
    rid = (await _upload(client, h, sid, audio_dir / "mono.wav")).json()["recording_id"]
    await _drain(engine, store)
    assert (await _recording(client, h, rid))["status_code"] == "too_long"


async def test_sha256_dedupe_returns_same_id(client, engine, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    first = await _upload(client, h, sid, audio_dir / "mono.mp3")
    second = await _upload(client, h, sid, audio_dir / "mono.mp3")  # different Idempotency-Key, same content
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["recording_id"] == first.json()["recording_id"] and second.json()["duplicate"] is True
    s = uuid.UUID(sid)
    assert await scalar(engine, "SELECT count(*) FROM audio_streams WHERE session_id = :s", s=s) == 1
    assert await scalar(engine, "SELECT count(*) FROM jobs WHERE session_id = :s AND kind = 'audio.ingest'", s=s) == 1


async def test_idempotency_replay_and_conflict(client, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    key = uuid.uuid4().hex
    a = await _upload(client, h, sid, audio_dir / "mono.ogg", key=key)
    b = await _upload(client, h, sid, audio_dir / "mono.ogg", key=key)
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    assert b.headers.get("Idempotent-Replay") == "true"
    c = await _upload(client, h, sid, audio_dir / "other.wav", key=key)
    assert c.status_code == 409 and c.json()["code"] == "conflict_idempotency_key"


async def test_second_recording_needs_replace(client, engine, store, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    old = (await _upload(client, h, sid, audio_dir / "mono.wav")).json()["recording_id"]
    r = await _upload(client, h, sid, audio_dir / "other.wav")
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    put = await client.put(f"/api/v1/recordings/{old}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                           files={"file": ("other.wav", (audio_dir / "other.wav").read_bytes())})
    assert put.status_code == 202, put.text
    new = put.json()["recording_id"]
    assert new != old
    assert (await _recording(client, h, old))["replaced_by"] == new
    cur = (await client.get(f"/api/v1/sessions/{sid}/recording", headers=h)).json()
    assert cur["recording_id"] == new
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'recording.replace' AND target_id = :t",
                        t=uuid.UUID(new)) == 1
    await _drain(engine, store)
    assert (await _recording(client, h, new))["status"] == "normalised"
    # Only the current recording can be replaced.
    again = await client.put(f"/api/v1/recordings/{old}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                             files={"file": ("m.mp3", (audio_dir / "mono.mp3").read_bytes())})
    assert again.status_code == 409


async def test_rerun_produces_identical_objects(client, engine, store, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    rid = (await _upload(client, h, sid, audio_dir / "mono.m4a")).json()["recording_id"]
    await _drain(engine, store)
    keys = [("audio", f"{sid}/{rid}/{n}") for n in ("normalized.wav", "normalized.flac")]
    before = [store.objects[k] for k in keys]
    async with engine.begin() as conn:
        from app.worker import queue

        await queue.enqueue(conn, "audio.ingest", {"audio_stream_id": rid}, session_id=uuid.UUID(sid),
                            dedupe_key=f"audio.ingest:{rid}")
    assert await _drain(engine, store) >= 1
    assert [store.objects[k] for k in keys] == before


async def test_snippet_plays_requested_range(client, engine, store, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    rid = (await _upload(client, h, sid, audio_dir / "mono.wav")).json()["recording_id"]
    early = await client.get(f"/api/v1/sessions/{sid}/audio", params={"from_ms": 0, "to_ms": 1000}, headers=h)
    assert early.status_code == 409  # not normalised yet
    await _drain(engine, store)
    r = await client.get(f"/api/v1/sessions/{sid}/audio", params={"from_ms": 1000, "to_ms": 2000}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] == 300 and body["url"].endswith(f"{sid}/{rid}/snippets/1000-2000.flac")
    snip = audio_dir / "snip.flac"
    snip.write_bytes(store.objects[("audio", f"{sid}/{rid}/snippets/1000-2000.flac")])
    assert abs(_duration_s(snip) - 1.0) <= 0.25
    bad = await client.get(f"/api/v1/sessions/{sid}/audio", params={"from_ms": 2000, "to_ms": 1000}, headers=h)
    assert bad.status_code == 422


async def test_recording_is_owner_only(client, audio_dir):  # type: ignore[no-untyped-def]
    h, sid = await _ready_session(client)
    rid = (await _upload(client, h, sid, audio_dir / "mono.wav")).json()["recording_id"]
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/recordings/{rid}", headers=other)).status_code == 404


async def _job(engine, rid):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT status, attempts FROM jobs WHERE dedupe_key = :k ORDER BY created_at DESC LIMIT 1"),
            {"k": f"audio.ingest:{rid}"})).first()
