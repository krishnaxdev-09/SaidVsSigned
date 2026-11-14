"""Claim endpoints (doc 04 §9, doc 11 §4): list claims, dismiss/restore a claim (audited)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import client_ip, current_user
from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.claims.service import claim_event, load_claims
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import get_owned_session
from app.schemas.claims import Claim

router = APIRouter(prefix="/api/v1", tags=["claims"])


class ClaimPatch(BaseModel):
    state: Literal["dismissed", "active"]
    user_note: str | None = Field(default=None, max_length=500)


@router.get("/sessions/{session_id}/claims", response_model=list[Claim])
async def list_claims(session_id: uuid.UUID, verifiable: bool | None = None, state: str | None = "active",
                      user_id: uuid.UUID = Depends(current_user)) -> list[Claim]:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        claims = await load_claims(conn, session_id)
    return [c for c in claims if (verifiable is None or c.verifiable == verifiable)
            and (state in (None, "all") or c.state == state)]


@router.patch("/claims/{claim_id}", response_model=Claim)
async def patch_claim(claim_id: uuid.UUID, body: ClaimPatch, request: Request,
                      user_id: uuid.UUID = Depends(current_user)) -> Claim:
    async with get_engine().begin() as conn:
        row = (await conn.execute(text(
            "SELECT c.session_id, c.state FROM claims c JOIN sessions s ON s.id = c.session_id "
            "WHERE c.id = :id AND s.owner_id = :u AND s.deleted_at IS NULL"), {"id": claim_id, "u": user_id})).first()
        if row is None:
            raise AppError("not_found", "Claim not found")
        if row.state == "superseded":
            raise AppError("invalid_state", "This claim belongs to an old transcript revision")
        await conn.execute(text("UPDATE claims SET state = :st, updated_at = now() WHERE id = :id"),
                           {"st": body.state, "id": claim_id})
        # Audit keeps ids and metadata only, never the note text (doc 12 §B.4).
        await audit.record(conn, "claim.dismiss", actor_id=user_id, target_type="claim", target_id=claim_id,
                           ip=client_ip(request), meta={"state": body.state, "note_provided": bool(body.user_note)})
        claim = (await load_claims(conn, row.session_id, claim_id=claim_id))[0]
        await publish(conn, row.session_id, EventType.CLAIM_UPDATED, claim_event(claim))
        if body.state == "active":  # restored claim: re-verify (dismissed claims keep their last result)
            from app.modules.verify.job import enqueue_verify

            await enqueue_verify(conn, row.session_id, claim_id, "claim_corrected")
    return claim
