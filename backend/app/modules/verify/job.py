"""Worker job `verify:{claim_id}` (doc 08 §9, doc 11 job table; T2.5) and the re-verification triggers.

Debounced 2 s and coalesced by `dedupe_key` (one queued + one running per claim). Triggers: document ready/replaced,
speaker confirmed, quote confirmed, transcript revised (claims re-extracted), claim corrected, manual re-verify.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.llm.base import EmbeddingProvider, LLMProvider
from app.modules.verify import service
from app.worker import queue
from app.worker.runner import Handler, JobContext

DEBOUNCE_S = 2.0


async def enqueue_verify(conn: AsyncConnection, session_id: uuid.UUID, claim_id: uuid.UUID, trigger: str) -> None:
    await queue.enqueue(conn, "verify", {"claim_id": str(claim_id), "trigger": trigger}, session_id=session_id,
                        dedupe_key=f"verify:{claim_id}", delay_seconds=DEBOUNCE_S)


async def enqueue_session(conn: AsyncConnection, session_id: uuid.UUID, trigger: str, *,
                          transcript_revision: int | None = None) -> int:
    """Re-verify every active claim of the session (optionally of one transcript revision)."""
    ids = (await conn.execute(text(
        "SELECT id FROM claims WHERE session_id = :s AND state = 'active' "
        "AND (CAST(:r AS int) IS NULL OR transcript_revision = :r)"), {"s": session_id, "r": transcript_revision})).scalars()
    n = 0
    for cid in ids:
        await enqueue_verify(conn, session_id, cid, trigger)
        n += 1
    return n


def make_verify_handler(engines: list[service.VerificationEngine], *, embedder: EmbeddingProvider | None = None,
                        llm: LLMProvider | None = None) -> Handler:
    async def handle(ctx: JobContext) -> None:
        await ctx.checkpoint()
        await service.verify_claim(ctx.conn, uuid.UUID(ctx.job.payload["claim_id"]),
                                   ctx.job.payload.get("trigger", "manual_reverify"), engines,
                                   embedder=embedder, llm=llm)

    return handle
