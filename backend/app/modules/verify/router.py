"""Results API — doc 08 §10 / doc 11 (T2.5), plus the HC-3 "play and confirm" quote confirmation (ADR-019)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import client_ip, current_user
from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.claims.service import claim_event, load_claims
from app.modules.consent.gate import require_consent
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import get_owned_session
from app.modules.verify import job, service
from app.schemas.claims import Claim
from app.schemas.verification import ReasonCode, Status, VerificationResult

router = APIRouter(prefix="/api/v1", tags=["results"])


@router.get("/sessions/{session_id}/results", response_model=list[VerificationResult])
async def list_results(session_id: uuid.UUID, status: Status | None = None, include_hidden: bool = False,
                       user_id: uuid.UUID = Depends(current_user)) -> list[VerificationResult]:
    """Current result per active claim. `not_agent_claim` results are hidden by default (doc 08 §3.1)."""
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        res = await service.load_results(conn, "r.is_current AND r.claim_id IN (SELECT id FROM claims WHERE "
                                               "session_id = :s AND state = 'active')", {"s": session_id})
    return [r for r in res if (status is None or r.status == status)
            and (include_hidden or r.reason_code != ReasonCode.NOT_AGENT_CLAIM)]


@router.get("/results/{result_id}/history", response_model=list[VerificationResult])
async def result_history(result_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> list[VerificationResult]:
    async with get_engine().connect() as conn:
        claim_id = (await conn.execute(text(
            "SELECT r.claim_id FROM verification_results r JOIN claims c ON c.id = r.claim_id "
            "JOIN sessions s ON s.id = c.session_id WHERE r.id = :id AND s.owner_id = :u AND s.deleted_at IS NULL"),
            {"id": result_id, "u": user_id})).scalar()
        if claim_id is None:
            raise AppError("not_found", "Result not found")
        return await service.load_results(conn, "r.claim_id = :c", {"c": claim_id})


class ReverifyOut(BaseModel):
    queued: int


@router.post("/sessions/{session_id}/reverify", response_model=ReverifyOut, status_code=202)
async def reverify(session_id: uuid.UUID = Depends(require_consent),
                   user_id: uuid.UUID = Depends(current_user)) -> ReverifyOut:
    async with get_engine().begin() as conn:
        await get_owned_session(conn, session_id, user_id)
        n = await job.enqueue_session(conn, session_id, "manual_reverify")
    return ReverifyOut(queued=n)


class QuoteConfirmIn(BaseModel):
    confirmed: bool


@router.post("/claims/{claim_id}/confirm-quote", response_model=Claim)
async def confirm_quote(claim_id: uuid.UUID, body: QuoteConfirmIn, request: Request,
                        user_id: uuid.UUID = Depends(current_user)) -> Claim:
    """HC-3 / ADR-019: after playing the original clip, the customer confirms the agent said these words. A numeric
    CONFLICT is never shown without it. Not offered for numeric claims in Hindi sessions (Hindi not yet supported)."""
    async with get_engine().begin() as conn:
        row = (await conn.execute(text(
            "SELECT c.session_id, c.state, s.language_hint, g.language FROM claims c JOIN sessions s ON s.id = c.session_id "
            "LEFT JOIN transcript_segments g ON g.id = CAST(c.quote_anchors->0->>'segment_id' AS uuid) "
            "WHERE c.id = :id AND s.owner_id = :u AND s.deleted_at IS NULL"), {"id": claim_id, "u": user_id})).first()
        if row is None:
            raise AppError("not_found", "Claim not found")
        if row.state != "active":
            raise AppError("invalid_state", "Only active claims can be confirmed")
        claim = (await load_claims(conn, row.session_id, claim_id=claim_id))[0]
        if claim.values and service.is_hindi(row.language_hint, row.language, claim.quote):
            raise AppError("invalid_state", "Quote confirmation isn't available for numbers in Hindi yet")
        await conn.execute(text("UPDATE claims SET quote_confirmed_by_user = :c, updated_at = now() WHERE id = :id"),
                           {"c": body.confirmed, "id": claim_id})
        await audit.record(conn, "claim.confirm_quote", actor_id=user_id, target_type="claim", target_id=claim_id,
                           ip=client_ip(request), meta={"confirmed": body.confirmed})
        claim = (await load_claims(conn, row.session_id, claim_id=claim_id))[0]
        await publish(conn, row.session_id, EventType.CLAIM_UPDATED, claim_event(claim))
        await job.enqueue_verify(conn, row.session_id, claim_id, "quote_confirmed")
    return claim
