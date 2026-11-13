"""Session endpoints (doc 11 §4a). Only mode=upload is accepted in the MVP; in_person/meet_tab -> 422 mode_not_available."""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.core.ids import uuid7
from app.modules.audit import service as audit
from app.modules.consent.service import ConsentStatus, consent_status
from app.modules.sessions.service import (
    SESSION_COLUMNS,
    LanguageHint,
    ProductType,
    Session,
    SessionMode,
    SessionState,
    get_owned_session,
    to_session,
)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreateIn(BaseModel):
    mode: SessionMode
    product_type: ProductType
    lender_name: str | None = Field(default=None, max_length=200)
    language_hint: LanguageHint


class SessionList(BaseModel):
    items: list[Session]
    next_cursor: str | None


class SessionStateOut(BaseModel):
    session: Session
    consent: ConsentStatus
    last_event_seq: int


@router.post("", status_code=201, response_model=Session)
async def create_session(body: SessionCreateIn, request: Request,
                         user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    async def handler() -> Session:
        if body.mode != "upload":
            raise AppError("mode_not_available", f"Mode '{body.mode}' is deferred (Later-L1/L2); MVP supports 'upload'")
        sid = uuid7()
        async with get_engine().begin() as conn:
            row = (await conn.execute(text(
                "INSERT INTO sessions (id, owner_id, mode, state, product_type, language_hint, lender_name_raw, "
                "retention_until) VALUES (:id, :u, :m, :st, :p, :l, :n, now() + make_interval(days => :d)) "
                f"RETURNING {SESSION_COLUMNS}"),
                {"id": sid, "u": user_id, "m": body.mode, "st": SessionState.CONSENT_PENDING.value,
                 "p": body.product_type, "l": body.language_hint, "n": body.lender_name,
                 "d": get_settings().RETENTION_DAYS})).mappings().one()
            await audit.record(conn, "session.create", actor_id=user_id, target_type="session", target_id=sid,
                               ip=client_ip(request))
        return to_session(row)

    return await idempotent_create(request, user_id, 201, handler)


@router.get("", response_model=SessionList)
async def list_sessions(cursor: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                        user_id: uuid.UUID = Depends(current_user)) -> SessionList:
    params: dict[str, object] = {"u": user_id, "l": limit + 1}
    where = "owner_id = :u AND deleted_at IS NULL"
    if cursor:
        try:
            params["c"] = uuid.UUID(base64.urlsafe_b64decode(cursor.encode()).decode())
        except ValueError as exc:
            raise AppError("validation_error", "Invalid cursor") from exc
        where += " AND id < :c"
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE {where} ORDER BY id DESC LIMIT :l"), params)).mappings().all()
    items = [to_session(r) for r in rows[:limit]]
    nxt = base64.urlsafe_b64encode(str(items[-1].id).encode()).decode() if len(rows) > limit else None
    return SessionList(items=items, next_cursor=nxt)


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> Session:
    async with get_engine().connect() as conn:
        return await get_owned_session(conn, session_id, user_id)


@router.get("/{session_id}/state", response_model=SessionStateOut)
async def get_session_state(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> SessionStateOut:
    async with get_engine().connect() as conn:
        session = await get_owned_session(conn, session_id, user_id)
        consent = await consent_status(conn, session_id)
        seq = (await conn.execute(text("SELECT coalesce(max(seq), 0) FROM events WHERE session_id = :s"),
                                  {"s": session_id})).scalar_one()
    return SessionStateOut(session=session, consent=consent, last_event_seq=int(seq))
