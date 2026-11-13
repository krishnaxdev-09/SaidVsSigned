"""Event outbox + LISTEN/NOTIFY fan-out (doc 01 §4 step 7, doc 11 §5).

publish(): INSERT into `events` and pg_notify inside the caller's transaction. Postgres delivers NOTIFY only on
commit, so a listener never sees an event whose row isn't visible yet.

Listener: one dedicated asyncpg connection (never a pooled one). Notifications are only wake-ups; subscribers then
read `events` by seq, so the table stays the source of truth and missed notifications cannot lose events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

import asyncpg
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.realtime.events import DEFERRED_EVENT_TYPES, EventEnvelope, EventType

log = structlog.get_logger()
CHANNEL = "session_events"


async def publish(conn: AsyncConnection, session_id: uuid.UUID, type_: EventType, payload: dict[str, Any]) -> int:
    if type_ in DEFERRED_EVENT_TYPES:
        raise ValueError(f"{type_} is deferred to Later-L1")
    seq = (await conn.execute(text(
        "INSERT INTO events (session_id, type, payload) VALUES (:s, :t, CAST(:p AS jsonb)) RETURNING seq"),
        {"s": session_id, "t": type_.value, "p": json.dumps(payload, default=str)})).scalar_one()
    await conn.execute(text("SELECT pg_notify(:c, :m)"), {"c": CHANNEL, "m": f"{session_id}:{seq}"})
    return int(seq)


async def fetch_since(conn: AsyncConnection, session_id: uuid.UUID, since: int, limit: int = 500) -> list[EventEnvelope]:
    rows = (await conn.execute(text(
        "SELECT seq, session_id, type, payload, ts FROM events WHERE session_id = :s AND seq > :n "
        "ORDER BY seq LIMIT :l"), {"s": session_id, "n": since, "l": limit})).mappings().all()
    return [EventEnvelope(type=r["type"], session_id=r["session_id"], seq=r["seq"], ts=r["ts"], payload=r["payload"])
            for r in rows]


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[uuid.UUID, set[asyncio.Event]] = defaultdict(set)
        self._conn: asyncpg.Connection | None = None
        self.connected = False
        self._task: asyncio.Task[None] | None = None
        self._dsn = ""

    def subscribe(self, session_id: uuid.UUID) -> asyncio.Event:
        ev = asyncio.Event()
        self._subs[session_id].add(ev)
        return ev

    def unsubscribe(self, session_id: uuid.UUID, ev: asyncio.Event) -> None:
        self._subs[session_id].discard(ev)
        if not self._subs[session_id]:
            self._subs.pop(session_id, None)

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            sid = uuid.UUID(payload.split(":", 1)[0])
        except ValueError:
            return
        for ev in list(self._subs.get(sid, ())):
            ev.set()

    def _wake_all(self) -> None:
        for evs in self._subs.values():
            for ev in evs:
                ev.set()

    async def start(self, dsn: str) -> None:
        self._dsn = dsn
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        backoff = 0.5
        while True:
            try:
                closed = asyncio.Event()
                self._conn = await asyncpg.connect(self._dsn)
                self._conn.add_termination_listener(lambda _c, ev=closed: ev.set())
                await self._conn.add_listener(CHANNEL, self._on_notify)
                self.connected = True
                backoff = 0.5
                self._wake_all()  # catch up anything missed while disconnected
                await closed.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("realtime_listener_reconnect", error_type=type(exc).__name__)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
        self.connected = False


hub = EventHub()
