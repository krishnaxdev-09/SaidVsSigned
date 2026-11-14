"""Worker job `claims.extract:{session_id}:{transcript_revision}` (doc 04 §5, §8, §10; T1.6)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.retry import SchemaError
from app.llm.base import LLMProvider
from app.modules.claims import extract, service
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.stt.base import WordToken
from app.worker import queue
from app.worker.runner import Handler, JobContext

log = structlog.get_logger()


async def enqueue_extract(conn: AsyncConnection, session_id: uuid.UUID, rev: int) -> None:
    await queue.enqueue(conn, "claims.extract", {"session_id": str(session_id), "transcript_revision": rev},
                        session_id=session_id, dedupe_key=f"claims.extract:{session_id}:{rev}")


class _Seg:
    def __init__(self, row: Any, words: list[WordToken]) -> None:
        self.id, self.speaker_label, self.text = row.id, row.speaker_label, row.text
        self.start_ms, self.end_ms, self.language = row.start_ms, row.end_ms, row.language
        self.words = words


async def load_segments(conn: AsyncConnection, session_id: uuid.UUID, rev: int) -> list[_Seg]:
    rows = (await conn.execute(text(
        "SELECT id, speaker_label, text, start_ms, end_ms, language FROM transcript_segments "
        "WHERE session_id = :s AND transcript_revision = :r ORDER BY start_ms, channel"), {"s": session_id, "r": rev})).all()
    words = (await conn.execute(text(
        "SELECT w.segment_id, w.text, w.start_ms, w.end_ms, w.confidence, w.speaker_label, w.is_numeric "
        "FROM transcript_words w JOIN transcript_segments g ON g.id = w.segment_id "
        "WHERE g.session_id = :s AND g.transcript_revision = :r ORDER BY w.segment_id, w.idx"),
        {"s": session_id, "r": rev})).all()
    by: dict[uuid.UUID, list[WordToken]] = {}
    for w in words:
        by.setdefault(w.segment_id, []).append(WordToken(text=w.text, start_ms=w.start_ms, end_ms=w.end_ms,
                                                          confidence=w.confidence, speaker_label=w.speaker_label,
                                                          is_numeric=w.is_numeric))
    return [_Seg(r, by.get(r.id, [])) for r in rows]


async def extract_window(llm: LLMProvider, core: list[Any], context: list[Any], rev: int,
                         drops: extract.DropLog) -> list[extract.BuiltClaim]:
    text_block, refs = extract.render_segments(core, context)
    messages = [*extract.fewshot_messages(), {"role": "user", "content": text_block}]
    try:
        out = await llm.structured(system=extract.SYSTEM_PROMPT, messages=messages,
                                   schema=extract.LLMWindowOutput, temperature=0.0, max_tokens=4000, timeout_s=120)
    except SchemaError:
        drops.extraction_failed_windows += 1  # doc 04 §10: drop the window after the corrective retry
        return []
    core_refs = {f"S{i}" for i in range(1, len(core) + 1)}
    built = [extract.build_claim(c, refs, core_refs, rev, drops) for c in out.claims]  # type: ignore[attr-defined]
    return [b for b in built if b is not None]


def make_extract_handler(llm: LLMProvider) -> Handler:
    async def handle(ctx: JobContext) -> None:
        session_id = uuid.UUID(ctx.job.payload["session_id"])
        rev = int(ctx.job.payload["transcript_revision"])
        current = (await ctx.conn.execute(text(
            "SELECT is_current FROM transcript_revisions WHERE session_id = :s AND revision = :r"),
            {"s": session_id, "r": rev})).scalar()
        if not current:
            return  # a newer revision exists; its own job extracts it
        segments = await load_segments(ctx.conn, session_id, rev)
        drops = extract.DropLog()
        claims: list[extract.BuiltClaim] = []
        for core, context in extract.windows(segments):
            await ctx.checkpoint()  # before each paid LLM call (doc 01 §5.4)
            claims += await extract_window(llm, core, context, rev, drops)
        await ctx.checkpoint()
        kept = extract.consolidate(claims)
        await service.replace_revision(ctx.conn, session_id, rev, kept)
        for c in await service.load_claims(ctx.conn, session_id, rev=rev):
            await publish(ctx.conn, session_id, EventType.CLAIM_CREATED, service.claim_event(c))
        # Logs carry counts and the prompt version only, never transcript text (doc 04 §3, doc 12 §B.2).
        log.info("claims_extracted", session_id=str(session_id), revision=rev, prompt_version=extract.PROMPT_VERSION,
                 segments=len(segments), raw=len(claims), kept=len(kept), **vars(drops))
        from app.modules.verify.job import enqueue_session  # doc 08 §9: transcript revised → (re-)verify

        await enqueue_session(ctx.conn, session_id, "transcript_revised", transcript_revision=rev)

    return handle
