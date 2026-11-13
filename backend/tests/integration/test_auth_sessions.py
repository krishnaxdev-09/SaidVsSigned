import uuid

import pytest

from tests.conftest import create_session, register_and_login, scalar

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_register_login_refresh_rotation_and_reuse_detection(client, engine):
    email, headers = await register_and_login(client)
    assert (await client.post("/api/v1/auth/register", json={"email": email, "password": "x" * 12})).json()["code"] == "email_taken"
    assert (await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})).status_code == 401
    old_cookie = client.cookies.get("ss_refresh")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 200 and "access_token" in r.json()
    new_cookie = client.cookies.get("ss_refresh")
    assert new_cookie and new_cookie != old_cookie
    # Reusing the rotated (revoked) token revokes the whole family
    client.cookies.set("ss_refresh", old_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    client.cookies.set("ss_refresh", new_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    uid = await scalar(engine, "SELECT id FROM users WHERE email = :e", e=email)
    # both presentations were of revoked tokens (the old one, then the family-revoked new one): each is audited
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'auth.refresh_reuse_detected' AND actor_id = :u", u=uid) == 2


async def test_logout_revokes_refresh(client):
    _, headers = await register_and_login(client)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_unauthenticated_and_bad_token(client):
    r = await client.get("/api/v1/sessions")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert r.headers["content-type"].startswith("application/problem+json")
    r = await client.get("/api/v1/sessions", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_session_create_list_get_and_state(client):
    _, headers = await register_and_login(client)
    s = await create_session(client, headers, lender_name="Demo Finance")
    assert s["state"] == "consent_pending" and s["mode"] == "upload" and s["lender_name"] == "Demo Finance"
    lst = (await client.get("/api/v1/sessions", headers=headers)).json()
    assert [i["id"] for i in lst["items"]] == [s["id"]]
    assert (await client.get(f"/api/v1/sessions/{s['id']}", headers=headers)).json()["id"] == s["id"]
    st = (await client.get(f"/api/v1/sessions/{s['id']}/state", headers=headers)).json()
    assert st["consent"]["active"] is False and st["last_event_seq"] == 0


async def test_session_pagination(client):
    _, headers = await register_and_login(client)
    ids = [(await create_session(client, headers))["id"] for _ in range(3)]
    p1 = (await client.get("/api/v1/sessions", params={"limit": 2}, headers=headers)).json()
    p2 = (await client.get("/api/v1/sessions", params={"limit": 2, "cursor": p1["next_cursor"]}, headers=headers)).json()
    assert [i["id"] for i in p1["items"] + p2["items"]] == list(reversed(ids)) and p2["next_cursor"] is None


async def test_deferred_modes_and_validation(client):
    _, headers = await register_and_login(client)
    for mode in ("in_person", "meet_tab"):
        r = await client.post("/api/v1/sessions", json={"mode": mode, "product_type": "personal_loan", "language_hint": "en"},
                              headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
        assert r.status_code == 422 and r.json()["code"] == "mode_not_available"
    r = await client.post("/api/v1/sessions", json={"mode": "upload", "product_type": "car", "language_hint": "en"},
                          headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


async def test_ownership_isolation_returns_404(client):
    _, a = await register_and_login(client)
    _, b = await register_and_login(client)
    s = await create_session(client, a)
    for path in (f"/api/v1/sessions/{s['id']}", f"/api/v1/sessions/{s['id']}/state"):
        r = await client.get(path, headers=b)
        assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert (await client.get("/api/v1/sessions", headers=b)).json()["items"] == []
    r = await client.post("/api/v1/auth/ws-ticket", json={"session_id": s["id"], "purpose": "events"}, headers=b)
    assert r.status_code == 404
