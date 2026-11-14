"""doc 07 §5: `GET /lenders/search?q=` (demo data) and `PUT /sessions/{id}/facts` (used by shadow rules only).
`GET /rules` is not exposed: the MVP has no user-facing rule output (admin listing deferred)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import current_user
from app.modules.rules.engine import DEMO_NOTE
from app.modules.sessions.service import get_owned_session

router = APIRouter(prefix="/api/v1", tags=["lenders"])


class LenderOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str | None
    source_url: str
    source_date: date | None
    note: str = DEMO_NOTE


@router.get("/lenders/search", response_model=list[LenderOut])
async def search_lenders(q: str = Query(min_length=2), user_id: uuid.UUID = Depends(current_user)) -> list[LenderOut]:
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id, name, category, source_url, source_date FROM public_entities WHERE demo_only "
            "AND word_similarity(normalized_name, lower(:q)) >= 0.3 ORDER BY word_similarity(normalized_name, lower(:q)) DESC "
            "LIMIT 5"), {"q": q})).all()
    return [LenderOut(id=r.id, name=r.name, category=r.category, source_url=r.source_url, source_date=r.source_date)
            for r in rows]


class FactsIn(BaseModel):
    borrower_type: Literal["individual", "business"] | None = None
    purpose: Literal["business", "non_business"] | None = None
    sanction_date: date | None = None


@router.put("/sessions/{session_id}/facts", response_model=FactsIn)
async def put_facts(session_id: uuid.UUID, body: FactsIn, user_id: uuid.UUID = Depends(current_user)) -> FactsIn:
    async with get_engine().begin() as conn:
        await get_owned_session(conn, session_id, user_id)
        await conn.execute(text("UPDATE sessions SET facts = CAST(:f AS jsonb) WHERE id = :s"),
                           {"f": json.dumps(body.model_dump(mode="json", exclude_none=True)), "s": session_id})
    return body
