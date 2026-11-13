"""Session persistence + ownership enforcement (doc 01 §4 step 5, doc 11 §3–4a).
Ownership failures return 404 not_found (never 403) so existence is not leaked (A4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError


class SessionState(StrEnum):
    CONSENT_PENDING = "consent_pending"
    READY = "ready"
    PROCESSING = "processing"  # set by later phases
    COMPLETED = "completed"  # set by later phases
    FAILED = "failed"  # terminal
    DELETED = "deleted"  # set by erase (Phase 4)


ProductType = Literal["personal_loan", "home_loan", "vehicle_loan", "business_loan", "gold_loan", "other_loan"]
LanguageHint = Literal["en", "hi", "hinglish"]
SessionMode = Literal["upload", "in_person", "meet_tab"]


class Session(BaseModel):
    id: uuid.UUID
    mode: SessionMode
    state: SessionState
    state_reason: str | None
    product_type: ProductType | None
    language_hint: LanguageHint | None
    lender_name: str | None
    retention_until: datetime
    created_at: datetime


SESSION_COLUMNS = ("id, mode, state, state_reason, product_type, language_hint, lender_name_raw AS lender_name, "
                   "retention_until, created_at")


def to_session(row: RowMapping) -> Session:
    return Session.model_validate(dict(row))


async def get_owned_session(conn: AsyncConnection, session_id: uuid.UUID, user_id: uuid.UUID, *,
                            for_update: bool = False) -> Session:
    sql = (f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = :id AND owner_id = :u AND deleted_at IS NULL"
           + (" FOR UPDATE" if for_update else ""))
    row = (await conn.execute(text(sql), {"id": session_id, "u": user_id})).mappings().first()
    if row is None:
        raise AppError("not_found", "Session not found")
    return to_session(row)


async def set_state(conn: AsyncConnection, session_id: uuid.UUID, state: SessionState, reason: str | None) -> None:
    await conn.execute(text("UPDATE sessions SET state = :s, state_reason = :r WHERE id = :id"),
                       {"s": state.value, "r": reason, "id": session_id})
