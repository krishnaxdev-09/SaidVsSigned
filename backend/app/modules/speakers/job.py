"""Worker job `speakers.suggest:{session_id}:{transcript_revision}` (doc 03 §5 step 2, §6 step 5; T1.3)."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.llm.base import LLMProvider
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.speakers import service
from app.worker import queue
from app.worker.runner import Handler, JobContext

log = structlog.get_logger()


async def enqueue_suggest(conn: AsyncConnection, session_id: uuid.UUID, rev: int) -> None:
    await queue.enqueue(conn, "speakers.suggest", {"session_id": str(session_id), "transcript_revision": rev},
                        session_id=session_id, dedupe_key=f"speakers.suggest:{session_id}:{rev}")


def make_suggest_handler(llm: LLMProvider | None) -> Handler:
    async def handle(ctx: JobContext) -> None:
        session_id = uuid.UUID(ctx.job.payload["session_id"])
        rev = int(ctx.job.payload["transcript_revision"])
        current = (await ctx.conn.execute(text(
            "SELECT is_current FROM transcript_revisions WHERE session_id = :s AND revision = :r"),
            {"s": session_id, "r": rev})).scalar()
        if not current:
            return  # the recording was replaced: a suggestion for an old revision is useless
        if await service.is_confirmed(ctx.conn, session_id, rev):
            return  # the customer already decided; never overwrite a confirmation
        segments = (await ctx.conn.execute(text(
            "SELECT speaker_label, text, start_ms, end_ms FROM transcript_segments "
            "WHERE session_id = :s AND transcript_revision = :r ORDER BY start_ms"), {"s": session_id, "r": rev})).all()
        await ctx.checkpoint()  # before the paid LLM call (doc 01 §5.4)
        suggestions, method = await service.suggest(llm, list(segments))
        await ctx.checkpoint()
        if not await service.write_auto(ctx.conn, session_id, rev, suggestions):
            return
        log.info("speaker_roles_suggested", session_id=str(session_id), revision=rev, method=method,
                 prompt_version=service.PROMPT_VERSION)
        await publish(ctx.conn, session_id, EventType.SPEAKER_UPDATED, {
            "transcript_revision": rev, "role_assignment": "auto", "method": method,
            "mapping": [{"speaker_label": s.speaker_label, "speaker_role": s.role,
                         "role_confidence": s.role_confidence} for s in suggestions]})

    return handle
