import uuid

import pytest
from sqlalchemy import text

from tests.conftest import create_session, current_texts, give_consent, register_and_login, scalar

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_consent_texts_current_versions(client):
    r = await client.get("/api/v1/consent-texts")
    kinds = {(t["kind"], t["locale"]) for t in r.json()}
    assert kinds == {("processing", "en"), ("processing", "hi"), ("attestation", "en"), ("attestation", "hi")}
    assert all(t["version"] == "0.1-draft" and len(t["body_sha256"]) == 64 for t in r.json())


async def test_grant_stores_versions_hashes_timestamps_and_audits(client, engine):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    r = await give_consent(client, h, s["id"], agent_informed="no")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["active"] is True and body["consent_text_version"] == "0.1-draft"
    assert body["attestation"]["agent_informed"] == "no" and body["attestation"]["attested_at"]
    assert body["attestation_note"] == ("The customer states the agent was not informed of the recording "
                                        "(not independently verified).")
    assert body["legal_caution"]
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT c.consent_text_sha256, c.attestation_text_sha256, c.granted_at, c.attested_at, c.method, c.party, "
            "pt.body_sha256 AS pt_sha, at.body_sha256 AS at_sha, c.client_meta FROM consents c "
            "JOIN consent_texts pt ON pt.id = c.consent_text_id JOIN consent_texts at ON at.id = c.attestation_text_id "
            "WHERE c.session_id = :s"), {"s": s["id"]})).mappings().one()
    assert row["consent_text_sha256"] == row["pt_sha"] and row["attestation_text_sha256"] == row["at_sha"]
    assert row["granted_at"] and row["attested_at"] and row["method"] == "in_app_checkbox" and row["party"] == "customer"
    assert set(row["client_meta"]) == {"user_agent_sha256"}  # no raw UA / IP in consents
    actions = await scalar(engine, "SELECT string_agg(action, ',' ORDER BY id) FROM audit_log WHERE target_id = :s",
                           s=s["id"])
    assert actions == "session.create,consent.grant,attestation.submit"
    sess = (await client.get(f"/api/v1/sessions/{s['id']}", headers=h)).json()
    assert sess["state"] == "ready"


async def test_grant_validation_and_state_errors(client):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    texts = await current_texts(client)
    base = {"consent_text_id": texts["processing"], "accepted": True,
            "attestation": {"attestation_text_id": texts["attestation"], "i_was_party": True, "agent_informed": "yes"}}

    async def post(body):
        return await client.post(f"/api/v1/sessions/{s['id']}/consents", json=body,
                                 headers={**h, "Idempotency-Key": uuid.uuid4().hex})

    assert (await post({**base, "accepted": False})).json()["code"] == "validation_error"
    assert (await post({**base, "attestation": {**base["attestation"], "i_was_party": False}})).json()["code"] == "validation_error"
    # wrong kind (attestation text used as processing text)
    assert (await post({**base, "consent_text_id": texts["attestation"]})).json()["code"] == "validation_error"
    assert (await post({**base, "attestation": {**base["attestation"], "agent_informed": "maybe"}})).status_code == 422
    assert (await post(base)).status_code == 201
    r = await post(base)  # already active
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"


async def test_consent_gate_test_route(client):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    r = await client.get(f"/api/v1/_test/sessions/{s['id']}/gated", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "consent_required"
    assert (await give_consent(client, h, s["id"])).status_code == 201
    assert (await client.get(f"/api/v1/_test/sessions/{s['id']}/gated", headers=h)).status_code == 200
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/_test/sessions/{s['id']}/gated", headers=other)).status_code == 404
    assert (await client.delete(f"/api/v1/sessions/{s['id']}/consents/customer", headers=h)).status_code == 200
    r = await client.get(f"/api/v1/_test/sessions/{s['id']}/gated", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "consent_required"


async def test_revocation_cancels_jobs_refuses_enqueue_and_is_terminal(client, engine):
    from app.worker import queue

    _, h = await register_and_login(client)
    s = await create_session(client, h)
    sid = uuid.UUID(s["id"])
    assert (await give_consent(client, h, s["id"])).status_code == 201
    async with engine.begin() as conn:
        q1 = await queue.enqueue(conn, "test.kind", {}, session_id=sid, dedupe_key=f"t:{sid}:1")
        q2 = await queue.enqueue(conn, "test.kind", {}, session_id=sid, dedupe_key=f"t:{sid}:2")
        running = await queue.enqueue(conn, "test.kind", {}, session_id=sid, dedupe_key=f"t:{sid}:3")
        await conn.execute(text("UPDATE jobs SET status='running', lease_expires_at = now() + interval '10 min' "
                                "WHERE id = :i"), {"i": running})
    r = await client.delete(f"/api/v1/sessions/{s['id']}/consents/customer", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["jobs_cancelled"] == 2 and out["jobs_cancel_requested"] == 1
    assert out["session"]["state"] == "failed" and out["session"]["state_reason"] == "consent_revoked"
    assert await scalar(engine, "SELECT count(*) FROM jobs WHERE id IN (:a, :b) AND status='cancelled'", a=q1, b=q2) == 2
    assert await scalar(engine, "SELECT cancel_requested_at IS NOT NULL FROM jobs WHERE id = :i", i=running)
    with pytest.raises(queue.EnqueueRefused):
        async with engine.begin() as conn:
            await queue.enqueue(conn, "test.kind", {}, session_id=sid)
    # terminal: cannot re-consent; cannot revoke twice
    assert (await give_consent(client, h, s["id"])).json()["code"] == "invalid_state"
    assert (await client.delete(f"/api/v1/sessions/{s['id']}/consents/customer", headers=h)).json()["code"] == "invalid_state"
    actions = await scalar(engine, "SELECT string_agg(action, ',' ORDER BY id) FROM audit_log WHERE target_id = :s", s=sid)
    assert actions.endswith("consent.revoke,jobs.cancelled")
    st = (await client.get(f"/api/v1/sessions/{s['id']}/state", headers=h)).json()
    assert st["consent"]["active"] is False and st["consent"]["revoked_at"]


async def test_consent_endpoint_is_idempotent(client, engine):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    texts = await current_texts(client)
    body = {"consent_text_id": texts["processing"], "accepted": True,
            "attestation": {"attestation_text_id": texts["attestation"], "i_was_party": True, "agent_informed": "unsure"}}
    key = uuid.uuid4().hex
    r1 = await client.post(f"/api/v1/sessions/{s['id']}/consents", json=body, headers={**h, "Idempotency-Key": key})
    r2 = await client.post(f"/api/v1/sessions/{s['id']}/consents", json=body, headers={**h, "Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 201 and r1.json() == r2.json()
    assert await scalar(engine, "SELECT count(*) FROM consents WHERE session_id = :s", s=s["id"]) == 1
