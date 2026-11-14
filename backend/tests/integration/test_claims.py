"""T1.6 claims pipeline end to end: upload → ingest → stt.batch (recorded ElevenLabs) → speakers.suggest +
claims.extract (recorded Gemma) → GET/PATCH claims (doc 04 §5, §8, §9)."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.llm.bedrock_mantle import BedrockMantleLLM
from tests.conftest import register_and_login, scalar
from tests.integration.test_transcripts import drain, ready_session, recorded_elevenlabs, upload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

FIX = Path(__file__).parents[1] / "fixtures" / "mantle"
SPEAKERS = json.loads((FIX / "speaker_roles_ag07.json").read_text())
CLAIMS = json.loads((FIX / "claims_ag07.json").read_text())


def recorded_gemma(calls: list[str] | None = None) -> BedrockMantleLLM:
    def handler(req: httpx.Request) -> httpx.Response:
        system = json.loads(req.content)["messages"][0]["content"]
        kind = "claims" if system.startswith("You extract loan-term") else "speakers"
        if calls is not None:
            calls.append(kind)
        return httpx.Response(200, json=CLAIMS if kind == "claims" else SPEAKERS)

    return BedrockMantleLLM(api_key="k", model="google.gemma-4-31b", base_url="https://mantle.test/openai/v1",
                            transport=httpx.MockTransport(handler))


async def pipeline(client, engine, store, files, calls=None):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    rid = await upload(client, h, sid, files["a"])
    await drain(engine, store, recorded_elevenlabs(store)[0], recorded_gemma(calls))
    return h, sid, rid


async def claims(client, h, sid, **params):  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/v1/sessions/{sid}/claims", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_claims_extracted_with_anchors_and_normalised_values(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    calls: list[str] = []
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files, calls)
    assert calls.count("claims") == 1 and calls.count("speakers") == 1
    cs = await claims(client, h, sid)
    by = {c["claim_type"]: c for c in cs}
    assert {"loan_amount", "processing_fee", "emi", "insurance_bundled", "lender_identity"} <= set(by)
    assert Decimal(by["loan_amount"]["values"][0]["value"]) == 1_000_000
    assert Decimal(by["processing_fee"]["values"][0]["value"]) == 1
    assert by["processing_fee"]["values"][0]["normaliser_version"] == "norm-1.0"
    transcript = (await client.get(f"/api/v1/sessions/{sid}/transcript", headers=h)).json()
    seg_text = {g["id"]: g["text"] for g in transcript["segments"]}
    for c in cs:  # property: every stored quote equals its anchored text (doc 04 §11)
        assert c["quote"] == " ".join(seg_text[a["segment_id"]][a["start_char"]:a["end_char"]] for a in c["quote_anchors"])
        assert all(a["transcript_revision"] == 1 for a in c["quote_anchors"])
        assert c["prompt_version"] == "claim_extract_v2" and c["state"] == "active"
    assert by["processing_fee"]["readiness"] == "needs_document" and by["lender_identity"]["readiness"] == "checkable_now"
    question = next(c for c in cs if c["utterance_kind"] == "question")
    assert question["verifiable"] is False
    n = await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'claim.created'",
                     s=uuid.UUID(sid))
    assert n == len(cs)


async def test_speaker_confirmation_changes_roles_without_re_extraction(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    calls: list[str] = []
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files, calls)
    fee = next(c for c in await claims(client, h, sid) if c["claim_type"] == "processing_fee" and c["verifiable"])
    assert (fee["speaker_role"], fee["role_assignment"]) == ("agent", "auto")
    r = await client.put(f"/api/v1/sessions/{sid}/speakers", headers=h,
                         json={"transcript_revision": 1, "roles": {"speaker_0": "customer", "speaker_1": "agent"}})
    assert r.status_code == 200
    fee2 = next(c for c in await claims(client, h, sid, state="all") if c["id"] == fee["id"])
    assert (fee2["speaker_role"], fee2["role_assignment"], fee2["verifiable"]) == ("customer", "user_confirmed", False)
    assert calls.count("claims") == 1  # no re-extraction


async def test_rerun_rewrites_the_revision(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    before = await claims(client, h, sid)
    async with engine.begin() as conn:
        from app.modules.claims.job import enqueue_extract

        await enqueue_extract(conn, uuid.UUID(sid), 1)
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0], recorded_gemma())
    after = await claims(client, h, sid)
    assert len(after) == len(before)
    assert await scalar(engine, "SELECT count(*) FROM claims WHERE session_id = :s", s=uuid.UUID(sid)) == len(after)


async def test_new_revision_supersedes_old_claims(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, rid = await pipeline(client, engine, fake_store, tone_files)
    old = await claims(client, h, sid)
    put = await client.put(f"/api/v1/recordings/{rid}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                           files={"file": ("b.wav", tone_files["b"].read_bytes())})
    assert put.status_code == 202
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0], recorded_gemma())
    active = await claims(client, h, sid)
    assert active and all(c["transcript_revision"] == 2 for c in active)
    everything = await claims(client, h, sid, state="all")
    superseded = [c for c in everything if c["state"] == "superseded"]
    assert {c["id"] for c in superseded} == {c["id"] for c in old}  # kept for audit, not deleted


async def test_dismiss_is_audited_and_owner_only(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    cid = (await claims(client, h, sid))[0]["id"]
    _, other = await register_and_login(client)
    assert (await client.patch(f"/api/v1/claims/{cid}", headers=other, json={"state": "dismissed"})).status_code == 404
    assert (await client.get(f"/api/v1/sessions/{sid}/claims", headers=other)).status_code == 404
    r = await client.patch(f"/api/v1/claims/{cid}", headers=h, json={"state": "dismissed", "user_note": "not relevant"})
    assert r.status_code == 200 and r.json()["state"] == "dismissed"
    assert cid not in {c["id"] for c in await claims(client, h, sid)}
    meta = await scalar(engine, "SELECT meta FROM audit_log WHERE action = 'claim.dismiss' AND target_id = :t",
                        t=uuid.UUID(cid))
    assert meta == {"state": "dismissed", "note_provided": True}  # the note text itself is never stored in audit


async def test_extraction_cancelled_on_revocation_keeps_nothing(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    await upload(client, h, sid, tone_files["a"])

    class RevokingGemma(BedrockMantleLLM):
        async def structured(self, **kw):  # type: ignore[no-untyped-def,override]
            if kw["system"].startswith("You extract loan-term"):
                await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)
            return await super().structured(**kw)

    llm = recorded_gemma()
    llm.__class__ = RevokingGemma
    await drain(engine, fake_store, recorded_elevenlabs(fake_store)[0], llm)
    assert await scalar(engine, "SELECT count(*) FROM claims WHERE session_id = :s", s=uuid.UUID(sid)) == 0
    assert await scalar(engine, "SELECT status FROM jobs WHERE kind = 'claims.extract' AND session_id = :s",
                        s=uuid.UUID(sid)) == "cancelled"


async def test_customer_question_never_eligible_even_if_speaker_swapped(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    """User clarification 2026-11-14: customer questions stay for context and never back a CONFLICT."""
    from app.modules.claims.eligibility import can_back_conflict
    from app.schemas.claims import Claim

    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    q = next(c for c in await claims(client, h, sid) if c["utterance_kind"] == "question")
    swap = {"speaker_0": "customer", "speaker_1": "agent"}  # confirm the questioner as the agent
    assert (await client.put(f"/api/v1/sessions/{sid}/speakers", headers=h,
                             json={"transcript_revision": 1, "roles": swap})).status_code == 200
    q2 = next(c for c in await claims(client, h, sid) if c["id"] == q["id"])
    assert q2["speaker_role"] == "agent" and q2["role_assignment"] == "user_confirmed"
    assert q2["verifiable"] is False and not can_back_conflict(Claim.model_validate(q2))
