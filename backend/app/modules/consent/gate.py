"""Reusable consent gate (doc 01 §5.5). Phase 1+ processing endpoints MUST depend on `require_consent`.

Phase 0 has no upload endpoints; the gate is verified against a test-only route (doc 01 §11, A11)."""

from __future__ import annotations

import uuid

from fastapi import Depends

from app.core.db import get_engine
from app.core.deps import current_user
from app.core.errors import AppError
from app.modules.consent.service import has_active_consent
from app.modules.sessions.service import get_owned_session


async def require_consent(session_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user)) -> uuid.UUID:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)  # 404 if not owned
        if not await has_active_consent(conn, session_id):
            raise AppError("consent_required", "Active customer consent and recording attestation are required")
    return session_id
