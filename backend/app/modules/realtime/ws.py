"""Events WebSocket `/ws/sessions/{id}/events?ticket=&since=` (doc 11 §5, v0.2.2 A9/A10).

- Auth: single-use ticket bound to user + session + purpose=events. Consent is NOT required.
- Close codes: 4401 bad/expired/used ticket; 4404 session not found / not owned.
- Replays events with seq > since, then streams live events. NOTIFY is only a wake-up; rows are read from `events`.
- Heartbeat: server sends {"type":"ping"} every 20 s.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.core.db import get_engine
from app.core.security import token_hash
from app.modules.realtime.bus import fetch_since, hub

router = APIRouter()
HEARTBEAT_SECONDS = 20.0


async def _consume_ticket(session_id: uuid.UUID, ticket: str) -> tuple[uuid.UUID, bool] | None:
    async with get_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE ws_tickets SET used_at = now() WHERE ticket_hash = :h AND used_at IS NULL AND expires_at > now() "
            "AND purpose = 'events' AND session_id = :s RETURNING user_id"),
            {"h": token_hash(ticket), "s": session_id})).first()
        if row is None:
            return None
        owned = (await conn.execute(text(
            "SELECT 1 FROM sessions WHERE id = :s AND owner_id = :u AND deleted_at IS NULL"),
            {"s": session_id, "u": row.user_id})).first()
        return row.user_id, owned is not None


@router.websocket("/ws/sessions/{session_id}/events")
async def events_ws(websocket: WebSocket, session_id: uuid.UUID, ticket: str = "", since: int = 0) -> None:
    await websocket.accept()
    result = await _consume_ticket(session_id, ticket) if ticket else None
    if result is None:
        await websocket.close(code=4401)
        return
    if not result[1]:
        await websocket.close(code=4404)
        return

    wake = hub.subscribe(session_id)
    last = max(since, 0)

    async def reader() -> None:
        while True:
            await websocket.receive_text()  # pongs/pings from client; disconnect raises

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            wake.clear()
            async with get_engine().connect() as conn:
                batch = await fetch_since(conn, session_id, last)
            for env in batch:
                await websocket.send_text(env.model_dump_json())
                last = env.seq
            if len(batch) == 500:
                continue  # more pending
            waiter = asyncio.create_task(wake.wait())
            done, _ = await asyncio.wait({waiter, reader_task}, timeout=HEARTBEAT_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            if reader_task in done:
                waiter.cancel()
                break
            if not done:
                waiter.cancel()
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(session_id, wake)
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader_task
