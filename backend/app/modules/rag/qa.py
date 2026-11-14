"""Ask API + job — doc 09 §6: POST /sessions/{id}/qa (Idempotency-Key) → 202 {qa_id}; job `qa.answer:{qa_id}`;
event qa.answer{qa_id, status: routing|retrieving|answering|done|failed, answer?}; GET /sessions/{id}/qa."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import current_user, idempotent_create
from app.llm.base import EmbeddingProvider, LLMProvider
from app.modules.consent.gate import require_consent
from app.modules.rag import ask
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import get_owned_session
from app.worker import queue
from app.worker.runner import Handler, JobContext

router = APIRouter(prefix="/api/v1", tags=["qa"])
SUGGESTED = ["What is the processing fee?", "Is there a foreclosure charge?", "What happens if I pay an EMI late?",
             "Is insurance deducted from my loan?"]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class QAOut(BaseModel):
    qa_id: uuid.UUID
    question: str
    route: str | None = None
    status: str
    answer: dict[str, Any] | None = None
    created_at: datetime | None = None


@router.post("/sessions/{session_id}/qa", status_code=202, response_model=QAOut)
async def ask_question(body: AskIn, request: Request, session_id: uuid.UUID = Depends(require_consent),
                       user_id: uuid.UUID = Depends(current_user)):  # type: ignore[no-untyped-def]
    async def handler() -> QAOut:
        async with get_engine().begin() as conn:
            await get_owned_session(conn, session_id, user_id)
            qid = uuid.uuid4()
            await conn.execute(text("INSERT INTO qa_messages (id, session_id, question, status) VALUES (:id, :s, :q, "
                                    "'routing')"), {"id": qid, "s": session_id, "q": body.question})
            await queue.enqueue(conn, "qa.answer", {"qa_id": str(qid)}, session_id=session_id, dedupe_key=f"qa:{qid}")
            await publish(conn, session_id, EventType.QA_ANSWER, {"qa_id": str(qid), "status": "routing"})
            return QAOut(qa_id=qid, question=body.question, status="routing")

    return await idempotent_create(request, user_id, 202, handler)


@router.get("/sessions/{session_id}/qa", response_model=list[QAOut])
async def list_qa(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> list[QAOut]:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        rows = (await conn.execute(text("SELECT * FROM qa_messages WHERE session_id = :s ORDER BY created_at"),
                                   {"s": session_id})).mappings().all()
    return [QAOut(qa_id=r["id"], question=r["question"], route=r["route"], status=r["status"], answer=r["answer"],
                  created_at=r["created_at"]) for r in rows]


@router.get("/sessions/{session_id}/qa/suggestions", response_model=list[str])
async def suggestions(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> list[str]:
    """Suggested questions: clarification questions from UNCLEAR / NOT_VERIFIED results plus a fixed list."""
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        qs = (await conn.execute(text(
            "SELECT DISTINCT r.suggested_question FROM verification_results r JOIN claims c ON c.id = r.claim_id "
            "WHERE c.session_id = :s AND r.is_current AND r.status IN ('UNCLEAR', 'NOT_VERIFIED') "
            "AND r.suggested_question IS NOT NULL AND r.reason_code <> 'not_agent_claim'"), {"s": session_id})).scalars().all()
    return [*sorted(qs)[:3], *SUGGESTED]


def make_qa_handler(embedder: EmbeddingProvider, llm: LLMProvider | None) -> Handler:
    async def handle(ctx: JobContext) -> None:
        qid = uuid.UUID(ctx.job.payload["qa_id"])
        row = (await ctx.conn.execute(text(
            "SELECT q.session_id, q.question, q.status, s.owner_id FROM qa_messages q JOIN sessions s ON s.id = q.session_id "
            "WHERE q.id = :id"), {"id": qid})).first()
        if row is None or row.status in ("done", "failed"):
            return
        from app.modules.verify.service import visibility

        a = await ask.answer(row.question, conn=ctx.conn, embedder=embedder, llm=llm, session_id=row.session_id,
                             user_id=row.owner_id, finance_visible=visibility()[0])
        await ctx.checkpoint()
        payload = {k: v for k, v in asdict(a).items() if k not in ("validator_errors", "attempts")}
        await ctx.conn.execute(text("UPDATE qa_messages SET route = :r, answer = CAST(:a AS jsonb), status = 'done' "
                                    "WHERE id = :id"), {"r": a.route, "a": json.dumps(payload), "id": qid})
        await publish(ctx.conn, row.session_id, EventType.QA_ANSWER, {"qa_id": str(qid), "status": "done", "answer": payload})

    return handle
