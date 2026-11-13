import asyncio
import json
import time

import pytest
import websockets

from tests.conftest import create_session, give_consent, register_and_login

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _ticket(client, h, sid):
    r = await client.post("/api/v1/auth/ws-ticket", json={"session_id": sid, "purpose": "events"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def _ws_url(server, sid, ticket, since=0):
    return server.replace("http", "ws") + f"/ws/sessions/{sid}/events?ticket={ticket}&since={since}"


async def test_session_status_event_delivered_within_200ms_of_consent_commit(client, server):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    async with websockets.connect(_ws_url(server, s["id"], await _ticket(client, h, s["id"]))) as ws:
        await asyncio.sleep(0.2)  # subscription established
        r = await give_consent(client, h, s["id"])
        committed_at = time.perf_counter()  # response is sent after the commit
        assert r.status_code == 201
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        latency = time.perf_counter() - committed_at
    assert msg["type"] == "session.status" and msg["payload"] == {"state": "ready"}
    assert msg["session_id"] == s["id"] and msg["seq"] > 0
    assert latency < 0.2, f"event latency {latency * 1000:.0f} ms"


async def test_resume_with_since_replays_only_newer(client, server):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    await give_consent(client, h, s["id"])
    await client.delete(f"/api/v1/sessions/{s['id']}/consents/customer", headers=h)
    async with websockets.connect(_ws_url(server, s["id"], await _ticket(client, h, s["id"]))) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 2))
        second = json.loads(await asyncio.wait_for(ws.recv(), 2))
    assert [first["payload"]["state"], second["payload"]["state"]] == ["ready", "failed"]
    assert second["payload"]["reason"] == "consent_revoked" and second["seq"] > first["seq"]
    async with websockets.connect(_ws_url(server, s["id"], await _ticket(client, h, s["id"]), since=first["seq"])) as ws:
        replay = json.loads(await asyncio.wait_for(ws.recv(), 2))
    assert replay["seq"] == second["seq"]


async def test_ticket_single_use_and_wrong_owner(client, server):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    ticket = await _ticket(client, h, s["id"])
    async with websockets.connect(_ws_url(server, s["id"], ticket)):
        pass
    async with websockets.connect(_ws_url(server, s["id"], ticket)) as ws:  # reuse
        with pytest.raises(websockets.ConnectionClosed) as ei:
            await asyncio.wait_for(ws.recv(), 2)
    assert ei.value.rcvd.code == 4401
    other = await create_session(client, h)
    async with websockets.connect(_ws_url(server, other["id"], await _ticket(client, h, s["id"]))) as ws:  # wrong session
        with pytest.raises(websockets.ConnectionClosed) as ei:
            await asyncio.wait_for(ws.recv(), 2)
    assert ei.value.rcvd.code == 4401


async def test_events_ws_does_not_require_consent(client, server):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    async with websockets.connect(_ws_url(server, s["id"], await _ticket(client, h, s["id"]))) as ws:
        with pytest.raises(asyncio.TimeoutError):  # open, no events yet, not closed
            await asyncio.wait_for(ws.recv(), 0.5)
