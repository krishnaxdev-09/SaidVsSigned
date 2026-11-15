"""Live microphone capture: chunks streamed in any order → finish → the normal recording pipeline (source live_mic)."""

from __future__ import annotations

import subprocess
import uuid

import pytest

from tests.conftest import register_and_login, scalar
from tests.integration.test_transcripts import ready_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def webm(tmp_path_factory):  # type: ignore[no-untyped-def]
    p = tmp_path_factory.mktemp("live") / "mic.webm"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-ac", "1", "-c:a", "libopus", str(p)], check=True)
    return p.read_bytes()


def k() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


async def test_live_capture_flow(client, engine, fake_store, webm):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    r = await client.post(f"/api/v1/sessions/{sid}/live", headers={**h, **k()})
    assert r.status_code == 201, r.text
    lid = r.json()["live_id"]
    parts = [webm[i:i + len(webm) // 3 + 1] for i in range(0, len(webm), len(webm) // 3 + 1)]
    for seq in (2, 0, 1):  # out of order, as retries would deliver them
        r = await client.put(f"/api/v1/live/{lid}/chunks/{seq}", headers=h, content=parts[seq])
        assert r.status_code == 200, r.text
    assert (await client.put(f"/api/v1/live/{lid}/chunks/1", headers=h, content=parts[1])).json()["chunks"] == 3  # resend ok
    r = await client.post(f"/api/v1/live/{lid}/finish", headers=h, json={"chunks": 4})
    assert r.status_code == 422 and "Missing chunks [3]" in r.json()["detail"]
    r = await client.post(f"/api/v1/live/{lid}/finish", headers=h, json={"chunks": 3})
    assert r.status_code == 202, r.text
    rid = r.json()["recording_id"]
    assert await scalar(engine, "SELECT source FROM audio_streams WHERE id = :i", i=uuid.UUID(rid)) == "live_mic"
    original = await scalar(engine, "SELECT object_key_original FROM audio_streams WHERE id = :i", i=uuid.UUID(rid))
    assert fake_store.objects[("audio", original)] == webm  # assembled byte-for-byte in order
    assert not any(key[1].startswith(f"{sid}/live/") for key in fake_store.objects)  # pieces deleted
    assert await scalar(engine, "SELECT count(*) FROM jobs WHERE kind = 'audio.ingest' AND session_id = :s",
                        s=uuid.UUID(sid)) == 1  # same pipeline as an upload
    rec = (await client.get(f"/api/v1/sessions/{sid}/recording", headers=h)).json()
    assert rec["recording_id"] == rid
    assert (await client.post(f"/api/v1/live/{lid}/finish", headers=h, json={"chunks": 3})).json()["duplicate"] is True
    assert (await client.post(f"/api/v1/sessions/{sid}/live", headers={**h, **k()})).status_code == 409  # one recording


async def test_live_abort_owner_and_consent(client, engine, fake_store, webm):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    lid = (await client.post(f"/api/v1/sessions/{sid}/live", headers={**h, **k()})).json()["live_id"]
    assert (await client.put(f"/api/v1/live/{lid}/chunks/0", headers=h, content=webm)).status_code == 200
    _, other = await register_and_login(client)
    assert (await client.put(f"/api/v1/live/{lid}/chunks/1", headers=other, content=b"x")).status_code == 404
    assert (await client.delete(f"/api/v1/live/{lid}", headers=h)).status_code == 204
    assert not any(key[1].startswith(f"{sid}/live/") for key in fake_store.objects)
    assert (await client.put(f"/api/v1/live/{lid}/chunks/1", headers=h, content=b"x")).status_code == 409
    lid2 = (await client.post(f"/api/v1/sessions/{sid}/live", headers={**h, **k()})).json()["live_id"]
    await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)  # revocation closes the capture
    assert await scalar(engine, "SELECT status FROM live_captures WHERE id = :l", l=uuid.UUID(lid2)) == "aborted"
    r = await client.post(f"/api/v1/sessions/{sid}/live", headers={**h, **k()})
    assert r.status_code == 403 and r.json()["code"] == "consent_required"
