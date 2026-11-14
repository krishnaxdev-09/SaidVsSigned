"""Consent endpoints (doc 01 §5, doc 11 §4a): consent texts, grant (consent + attestation in one request), revoke."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.core.ids import uuid7
from app.modules.audit import service as audit
from app.modules.consent.service import (
    AgentInformed,
    Attestation,
    ConsentStatus,
    RecordingSource,
    attestation_json,
    consent_status,
    has_active_consent,
    sha256_text,
)
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import Session, SessionState, get_owned_session, set_state
from app.worker.queue import cancel_session_jobs

router = APIRouter(prefix="/api/v1", tags=["consent"])


class ConsentText(BaseModel):
    id: uuid.UUID
    kind: Literal["processing", "attestation"]
    version: str
    locale: str
    body: str
    body_sha256: str


class AttestationIn(BaseModel):
    attestation_text_id: uuid.UUID
    i_was_party: bool
    agent_informed: AgentInformed
    recorded_on: date | None = None
    recording_source: RecordingSource | None = None


class ConsentIn(BaseModel):
    consent_text_id: uuid.UUID
    accepted: bool
    attestation: AttestationIn


class RevokeOut(BaseModel):
    session: Session
    jobs_cancelled: int
    jobs_cancel_requested: int


_CURRENT_TEXT_SQL = """
SELECT DISTINCT ON (kind, locale) id, kind, version, locale, body, body_sha256 FROM consent_texts
WHERE retired_at IS NULL AND (effective_from IS NULL OR effective_from <= now())
  AND (CAST(:k AS text) IS NULL OR kind = :k) AND (CAST(:l AS text) IS NULL OR locale = :l)
ORDER BY kind, locale, effective_from DESC NULLS LAST
"""


@router.get("/consent-texts", response_model=list[ConsentText])
async def get_consent_texts(kind: Literal["processing", "attestation"] | None = None,
                            locale: str | None = Query(default=None, max_length=10)) -> list[ConsentText]:
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text(_CURRENT_TEXT_SQL), {"k": kind, "l": locale})).mappings().all()
    return [ConsentText(**r) for r in rows]


async def _current_text(conn, text_id: uuid.UUID, kind: str):  # type: ignore[no-untyped-def]
    row = (await conn.execute(text(
        "SELECT id, version, locale, body, body_sha256, retired_at, effective_from FROM consent_texts "
        "WHERE id = :id AND kind = :k"), {"id": text_id, "k": kind})).mappings().first()
    if row is None or row["retired_at"] is not None:
        raise AppError("validation_error", f"{kind} text is unknown, retired or of the wrong kind")
    if sha256_text(row["body"]) != row["body_sha256"]:
        raise AppError("internal_error", "Consent text integrity check failed")
    return row


@router.post("/sessions/{session_id}/consents", status_code=201, response_model=ConsentStatus)
async def grant_consent(session_id: uuid.UUID, body: ConsentIn, request: Request,
                        user_id: uuid.UUID = Depends(current_user)) -> JSONResponse:
    async def handler() -> ConsentStatus:
        if body.accepted is not True:
            raise AppError("validation_error", "Consent must be accepted")
        if body.attestation.i_was_party is not True:
            raise AppError("validation_error", "Uploads are only allowed for conversations the customer took part in")
        async with get_engine().begin() as conn:
            session = await get_owned_session(conn, session_id, user_id, for_update=True)
            if session.state != SessionState.CONSENT_PENDING or await has_active_consent(conn, session_id):
                raise AppError("invalid_state", f"Consent cannot be given in state '{session.state.value}'")
            ptext = await _current_text(conn, body.consent_text_id, "processing")
            atext = await _current_text(conn, body.attestation.attestation_text_id, "attestation")
            att = Attestation(**body.attestation.model_dump(exclude={"attestation_text_id"}))
            ua = request.headers.get("user-agent", "")
            cid = uuid7()
            await conn.execute(text(
                "INSERT INTO consents (id, session_id, party, method, consent_text_id, consent_text_version, "
                "consent_text_sha256, locale, granted_at, actor_id, client_meta, attestation, attestation_text_id, "
                "attestation_text_sha256, attested_at) VALUES (:id, :s, 'customer', 'in_app_checkbox', :ct, :cv, "
                ":ch, :loc, now(), :u, CAST(:meta AS jsonb), CAST(:att AS jsonb), :at, :ah, now())"),
                {"id": cid, "s": session_id, "ct": ptext["id"], "cv": ptext["version"], "ch": ptext["body_sha256"],
                 "loc": ptext["locale"], "u": user_id,
                 "meta": f'{{"user_agent_sha256": "{sha256_text(ua)}"}}',
                 "att": attestation_json(att), "at": atext["id"], "ah": atext["body_sha256"]})
            await set_state(conn, session_id, SessionState.READY, None)
            ip = client_ip(request)
            await audit.record(conn, "consent.grant", actor_id=user_id, target_type="session", target_id=session_id,
                               meta={"consent_id": str(cid), "text_version": ptext["version"],
                                     "text_sha256": ptext["body_sha256"]}, ip=ip)
            await audit.record(conn, "attestation.submit", actor_id=user_id, target_type="session",
                               target_id=session_id,
                               meta={"consent_id": str(cid), "text_version": atext["version"],
                                     "text_sha256": atext["body_sha256"], "agent_informed": att.agent_informed},
                               ip=ip)
            await publish(conn, session_id, EventType.SESSION_STATUS, {"state": SessionState.READY.value})
            return await consent_status(conn, session_id)

    return await idempotent_create(request, user_id, 201, handler)


@router.delete("/sessions/{session_id}/consents/customer", response_model=RevokeOut)
async def revoke_consent(session_id: uuid.UUID, request: Request,
                         user_id: uuid.UUID = Depends(current_user)) -> RevokeOut:
    async with get_engine().begin() as conn:
        await get_owned_session(conn, session_id, user_id, for_update=True)
        revoked = (await conn.execute(text(
            "UPDATE consents SET revoked_at = now() WHERE session_id = :s AND party = 'customer' "
            "AND revoked_at IS NULL RETURNING id"), {"s": session_id})).first()
        if revoked is None:
            raise AppError("invalid_state", "No active customer consent to revoke")
        await set_state(conn, session_id, SessionState.FAILED, "consent_revoked")
        cancelled, requested = await cancel_session_jobs(conn, session_id)
        # KI-1: unfinished recordings end as failed(consent_revoked); jobs never overwrite 'failed' (see the guards in
        # audio/ingest.py and stt/job.py). Finished recordings are left for erasure (T4.3).
        stopped = (await conn.execute(text(
            "UPDATE audio_streams SET status = 'failed', status_code = 'consent_revoked' WHERE session_id = :s "
            "AND deleted_at IS NULL AND status NOT IN ('ready', 'failed') RETURNING id"), {"s": session_id})).all()
        for rec in stopped:
            await publish(conn, session_id, EventType.RECORDING_STATUS,
                          {"recording_id": str(rec.id), "stage": "failed", "progress": 1.0, "code": "consent_revoked"})
        docs = (await conn.execute(text(  # same rule for documents still being processed (T2.1)
            "UPDATE documents SET status = 'failed', status_code = 'consent_revoked' WHERE session_id = :s "
            "AND deleted_at IS NULL AND status IN ('queued', 'sanitising') RETURNING id, version"), {"s": session_id})).all()
        for doc in docs:
            await publish(conn, session_id, EventType.DOCUMENT_STATUS, {
                "document_id": str(doc.id), "version": doc.version, "stage": "failed", "progress": 1.0,
                "code": "consent_revoked", "warnings": []})
        ip = client_ip(request)
        await audit.record(conn, "consent.revoke", actor_id=user_id, target_type="session", target_id=session_id,
                           meta={"consent_id": str(revoked.id)}, ip=ip)
        await audit.record(conn, "jobs.cancelled", actor_id=user_id, target_type="session", target_id=session_id,
                           meta={"cancelled": cancelled, "cancel_requested": requested}, ip=ip)
        await publish(conn, session_id, EventType.SESSION_STATUS,
                      {"state": SessionState.FAILED.value, "reason": "consent_revoked"})
        session = await get_owned_session(conn, session_id, user_id)
    return RevokeOut(session=session, jobs_cancelled=cancelled, jobs_cancel_requested=requested)
