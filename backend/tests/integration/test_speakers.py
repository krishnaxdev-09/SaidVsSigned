"""T1.3 speaker suggestion + customer confirmation (doc 03 §5, doc 08 §5 speaker gate).

Pipeline: upload → ingest → stt.batch (recorded ElevenLabs) → speakers.suggest (recorded Gemma) → GET/PUT speakers.
Covers confirmation, rejection (swap), missing confirmation, uncertain suggestions, revision changes, segment
overrides, validation, consent and ownership."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.core.retry import SchemaError
from app.llm.bedrock_mantle import BedrockMantleLLM
from app.modules.speakers import service
from app.modules.speakers.service import LLMRoleSuggestion
from tests.conftest import register_and_login, scalar
from tests.integration.test_transcripts import drain, ready_session, recorded_elevenlabs, upload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

GEMMA = json.loads((Path(__file__).parents[1] / "fixtures" / "mantle" / "speaker_roles_ag07.json").read_text())


def recorded_gemma() -> BedrockMantleLLM:
    return BedrockMantleLLM(api_key="k", model="google.gemma-4-31b", base_url="https://mantle.test/openai/v1",
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=GEMMA)))


class StubLLM:
    name = "stub"

    def __init__(self, result=None, error=None) -> None:  # type: ignore[no-untyped-def]
        self.result, self.error = result, error

    async def structured(self, **kw):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        return LLMRoleSuggestion.model_validate(self.result)


async def transcribed(client, engine, store, files, llm=None):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, files["a"])
    await drain(engine, store, recorded_elevenlabs(store)[0], llm or recorded_gemma())
    return h, sid, rid


async def speakers(client, h, sid, **params):  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/v1/sessions/{sid}/speakers", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def put_roles(client, h, sid, rev, roles):  # type: ignore[no-untyped-def]
    return await client.put(f"/api/v1/sessions/{sid}/speakers", headers=h,
                            json={"transcript_revision": rev, "roles": roles})


async def gate(engine, sid, rev, label, segment_id=None) -> bool:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return service.conflict_allowed(await service.resolve(conn, uuid.UUID(sid), rev, label, segment_id))


async def test_suggestion_then_one_tap_confirmation(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files)
    sp = await speakers(client, h, sid)
    assert sp["transcript_revision"] == 1 and sp["confirmed"] is False
    by = {s["speaker_label"]: s for s in sp["speakers"]}
    assert by["speaker_0"]["suggested_role"] == "agent" and by["speaker_1"]["suggested_role"] == "customer"
    assert not by["speaker_0"]["suggestion_uncertain"] and by["speaker_0"]["role_assignment"] == "auto"
    assert 1 <= len(by["speaker_0"]["quotes"]) <= 3 and by["speaker_0"]["speaker_source"] == "diarized"
    # Missing confirmation: an auto-suggested agent can never back a CONFLICT.
    assert not await gate(engine, sid, 1, "speaker_0")
    s = uuid.UUID(sid)
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'speaker.updated'", s=s) == 1

    r = await put_roles(client, h, sid, 1, {"speaker_0": "agent", "speaker_1": "customer"})
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] is True
    assert {x["speaker_label"]: x["role_assignment"] for x in r.json()["speakers"]} == {
        "speaker_0": "user_confirmed", "speaker_1": "user_confirmed"}
    assert await gate(engine, sid, 1, "speaker_0") and not await gate(engine, sid, 1, "speaker_1")
    meta = await scalar(engine, "SELECT meta FROM audit_log WHERE action = 'speaker.confirm' AND target_id = :s", s=s)
    assert meta["suggestion_followed"] is True and meta["transcript_revision"] == 1


async def test_rejecting_the_suggestion_swaps_roles(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files)
    r = await put_roles(client, h, sid, 1, {"speaker_0": "customer", "speaker_1": "agent"})
    assert r.status_code == 200
    assert not await gate(engine, sid, 1, "speaker_0") and await gate(engine, sid, 1, "speaker_1")
    meta = await scalar(engine, "SELECT meta FROM audit_log WHERE action = 'speaker.confirm' AND target_id = :s",
                        s=uuid.UUID(sid))
    assert meta["suggestion_followed"] is False and meta["suggested"]["speaker_0"] == "agent"


async def test_uncertain_suggestion_needs_confirmation(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    two_agents = StubLLM({"speakers": [{"speaker_label": "speaker_0", "role": "agent", "role_confidence": 0.9},
                                       {"speaker_label": "speaker_1", "role": "agent", "role_confidence": 0.9}]})
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files, llm=two_agents)
    sp = await speakers(client, h, sid)
    assert all(x["suggestion_uncertain"] for x in sp["speakers"]) and sp["confirmed"] is False
    assert not await gate(engine, sid, 1, "speaker_0") and not await gate(engine, sid, 1, "speaker_1")


async def test_llm_failure_falls_back_to_uncertain_heuristic(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files, llm=StubLLM(error=SchemaError("x")))
    sp = await speakers(client, h, sid)
    assert {x["speaker_label"]: x["suggested_role"] for x in sp["speakers"]} == {"speaker_0": "agent",
                                                                                "speaker_1": "customer"}
    assert all(x["suggestion_uncertain"] for x in sp["speakers"])


async def test_confirmation_is_tied_to_the_transcript_revision(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, rid = await transcribed(client, engine, fake_store, tone_files)
    assert (await put_roles(client, h, sid, 1, {"speaker_0": "agent", "speaker_1": "customer"})).status_code == 200
    put = await client.put(f"/api/v1/recordings/{rid}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                           files={"file": ("b.wav", tone_files["b"].read_bytes())})
    assert put.status_code == 202
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0], recorded_gemma())
    sp = await speakers(client, h, sid)
    assert sp["transcript_revision"] == 2 and sp["confirmed"] is False  # confirmation does not carry over
    assert not await gate(engine, sid, 2, "speaker_0")
    stale = await put_roles(client, h, sid, 1, {"speaker_0": "agent", "speaker_1": "customer"})
    assert stale.status_code == 409 and stale.json()["code"] == "invalid_state"
    assert (await speakers(client, h, sid, revision=1))["confirmed"] is True  # old revision kept for audit
    assert (await put_roles(client, h, sid, 2, {"speaker_0": "agent", "speaker_1": "customer"})).status_code == 200


async def test_rerun_suggestion_never_overwrites_confirmation(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files)
    await put_roles(client, h, sid, 1, {"speaker_0": "customer", "speaker_1": "agent"})
    async with engine.begin() as conn:
        from app.modules.speakers.job import enqueue_suggest

        await enqueue_suggest(conn, uuid.UUID(sid), 1)
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0], recorded_gemma())
    by = {x["speaker_label"]: x for x in (await speakers(client, h, sid))["speakers"]}
    assert by["speaker_1"]["speaker_role"] == "agent" and by["speaker_1"]["role_assignment"] == "user_confirmed"


async def test_segment_override(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files)
    seg = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()["segments"][1]  # speaker_1
    r = await client.patch(f"/api/v1/segments/{seg['id']}/speaker", headers=h, json={"speaker_role": "agent"})
    assert r.status_code == 200
    assert await gate(engine, sid, 1, "speaker_1", uuid.UUID(seg["id"]))  # the override wins for that segment
    assert not await gate(engine, sid, 1, "speaker_1")  # the rest of speaker_1 is still unconfirmed
    async with engine.connect() as conn:
        res = await service.resolve(conn, uuid.UUID(sid), 1, "speaker_1", uuid.UUID(seg["id"]))
    assert res.speaker_source == "user"
    assert (await speakers(client, h, sid))["overrides"] == [{"segment_id": seg["id"], "speaker_role": "agent"}]


async def test_validation_consent_and_ownership(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await transcribed(client, engine, fake_store, tone_files)
    partial = await put_roles(client, h, sid, 1, {"speaker_0": "agent"})
    assert partial.status_code == 422
    bogus = await put_roles(client, h, sid, 1, {"speaker_0": "agent", "speaker_9": "customer"})
    assert bogus.status_code == 422
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/sessions/{sid}/speakers", headers=other)).status_code == 404
    assert (await put_roles(client, other, sid, 1, {"speaker_0": "agent", "speaker_1": "customer"})).status_code == 404
    assert (await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)).status_code == 200
    revoked = await put_roles(client, h, sid, 1, {"speaker_0": "agent", "speaker_1": "customer"})
    assert revoked.status_code == 403 and revoked.json()["code"] == "consent_required"


async def test_no_speakers_before_transcript(client):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    assert (await client.get(f"/api/v1/sessions/{sid}/speakers", headers=h)).status_code == 404
