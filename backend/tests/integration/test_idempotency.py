import asyncio
import uuid

import pytest

from tests.conftest import register_and_login, scalar

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
BODY = {"mode": "upload", "product_type": "personal_loan", "language_hint": "en"}


async def test_missing_key_is_400(client):
    _, h = await register_and_login(client)
    r = await client.post("/api/v1/sessions", json=BODY, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"


async def test_replay_returns_same_resource_without_duplicate(client, engine):
    _, h = await register_and_login(client)
    key = uuid.uuid4().hex
    r1 = await client.post("/api/v1/sessions", json=BODY, headers={**h, "Idempotency-Key": key})
    r2 = await client.post("/api/v1/sessions", json=BODY, headers={**h, "Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json() and r2.headers.get("idempotent-replay") == "true"
    assert await scalar(engine, "SELECT count(*) FROM sessions WHERE id = :i", i=r1.json()["id"]) == 1


async def test_same_key_different_body_is_409(client):
    _, h = await register_and_login(client)
    key = uuid.uuid4().hex
    assert (await client.post("/api/v1/sessions", json=BODY, headers={**h, "Idempotency-Key": key})).status_code == 201
    r = await client.post("/api/v1/sessions", json={**BODY, "language_hint": "hi"}, headers={**h, "Idempotency-Key": key})
    assert r.status_code == 409 and r.json()["code"] == "conflict_idempotency_key"


async def test_failed_request_does_not_store_key(client):
    _, h = await register_and_login(client)
    key = uuid.uuid4().hex
    bad = {**BODY, "mode": "in_person"}
    assert (await client.post("/api/v1/sessions", json=bad, headers={**h, "Idempotency-Key": key})).status_code == 422
    # same key can be retried with the same body (error not cached) and still fails the same way
    assert (await client.post("/api/v1/sessions", json=bad, headers={**h, "Idempotency-Key": key})).json()["code"] == "mode_not_available"


async def test_in_flight_duplicate_is_409_in_progress(client, engine):
    from sqlalchemy import text

    from app.core.idempotency import canonical_hash

    email, h = await register_and_login(client)
    uid = await scalar(engine, "SELECT id FROM users WHERE email = :e", e=email)
    key = uuid.uuid4().hex
    import json
    body = json.dumps(BODY).encode()
    async with engine.begin() as conn:  # simulate a request that is still being processed
        await conn.execute(text("INSERT INTO idempotency_keys (user_id, key, method, path, request_hash) "
                                "VALUES (:u, :k, 'POST', '/api/v1/sessions', :h)"),
                           {"u": uid, "k": key, "h": canonical_hash("POST", "/api/v1/sessions", body)})
    r = await client.post("/api/v1/sessions", content=body,
                          headers={**h, "Idempotency-Key": key, "content-type": "application/json"})
    assert r.status_code == 409 and r.json()["code"] == "idempotency_in_progress" and r.headers["retry-after"] == "2"


async def test_concurrent_same_key_creates_one_session(client, engine):
    email, h = await register_and_login(client)
    key = uuid.uuid4().hex
    rs = await asyncio.gather(*[client.post("/api/v1/sessions", json=BODY, headers={**h, "Idempotency-Key": key})
                                for _ in range(5)])
    codes = sorted(r.status_code for r in rs)
    assert set(codes) <= {201, 409} and 201 in codes
    uid = await scalar(engine, "SELECT id FROM users WHERE email = :e", e=email)
    assert await scalar(engine, "SELECT count(*) FROM sessions WHERE owner_id = :u", u=uid) == 1
