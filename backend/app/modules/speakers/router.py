"""Speaker endpoints (doc 11 §4: GET/PUT /sessions/{id}/speakers, PATCH /segments/{id}/speaker; doc 03 §5)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import get_engine
from app.core.deps import client_ip, current_user
from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.consent.gate import require_consent
from app.modules.consent.service import has_active_consent
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.sessions.service import get_owned_session
from app.modules.speakers import service
from app.modules.speakers.service import CONFIDENT, Role
from app.modules.verify.job import enqueue_session

router = APIRouter(prefix="/api/v1", tags=["speakers"])


class Quote(BaseModel):
    segment_id: uuid.UUID
    text: str
    start_ms: int
    end_ms: int


class SpeakerOut(BaseModel):
    speaker_label: str
    speaker_source: str
    segment_count: int
    suggested_role: Role | None
    role_confidence: float | None
    suggestion_uncertain: bool
    speaker_role: Role
    role_assignment: str
    confirmed_at: datetime | None
    quotes: list[Quote]


class SegmentOverride(BaseModel):
    segment_id: uuid.UUID
    speaker_role: Role


class SpeakersOut(BaseModel):
    transcript_revision: int
    confirmed: bool
    speakers: list[SpeakerOut]
    overrides: list[SegmentOverride]


class ConfirmIn(BaseModel):
    transcript_revision: int
    roles: dict[str, Role]


class SegmentRoleIn(BaseModel):
    speaker_role: Role


async def _current_revision(conn: AsyncConnection, session_id: uuid.UUID) -> int:
    rev = (await conn.execute(text(
        "SELECT revision FROM transcript_revisions WHERE session_id = :s AND is_current AND provider IS NOT NULL"),
        {"s": session_id})).scalar()
    if rev is None:
        raise AppError("not_found", "No transcript for this session yet")
    return int(rev)


async def _speakers(conn: AsyncConnection, session_id: uuid.UUID, rev: int) -> SpeakersOut:
    segs = (await conn.execute(text(
        "SELECT id, speaker_label, channel, text, start_ms, end_ms FROM transcript_segments "
        "WHERE session_id = :s AND transcript_revision = :r ORDER BY start_ms"), {"s": session_id, "r": rev})).all()
    rows = (await conn.execute(text(
        "SELECT speaker_label, segment_id, speaker_role, role_assignment, role_confidence, confirmed_at "
        "FROM speaker_assignments WHERE session_id = :s AND transcript_revision = :r AND is_current"),
        {"s": session_id, "r": rev})).all()
    auto = {r.speaker_label: r for r in rows if r.segment_id is None and r.role_assignment == "auto"}
    conf = {r.speaker_label: r for r in rows if r.segment_id is None and r.role_assignment == "user_confirmed"}
    by_label: dict[str, list] = defaultdict(list)  # type: ignore[type-arg]
    for g in segs:
        by_label[g.speaker_label or "unknown"].append(g)
    speakers = []
    for label, gs in by_label.items():
        a, c = auto.get(label), conf.get(label)
        chosen = c or a
        # 3 short quotes: the segments with the most words, shown in time order (doc 10 §5).
        picks = sorted(sorted(gs, key=lambda g: -len(g.text.split()))[:3], key=lambda g: g.start_ms)
        speakers.append(SpeakerOut(
            speaker_label=label, speaker_source="channel" if gs[0].channel != "mono" else "diarized",
            segment_count=len(gs), suggested_role=a.speaker_role if a else None,
            role_confidence=a.role_confidence if a else None,
            suggestion_uncertain=(a is None or a.speaker_role == "unknown" or (a.role_confidence or 0) < CONFIDENT),
            speaker_role=chosen.speaker_role if chosen is not None else "unknown",
            role_assignment="user_confirmed" if c else "auto", confirmed_at=c.confirmed_at if c else None,
            quotes=[Quote(segment_id=g.id, text=g.text[:200], start_ms=g.start_ms, end_ms=g.end_ms) for g in picks]))
    return SpeakersOut(transcript_revision=rev, confirmed=bool(conf), speakers=speakers,
                       overrides=[SegmentOverride(segment_id=r.segment_id, speaker_role=r.speaker_role)
                                  for r in rows if r.segment_id is not None])


@router.get("/sessions/{session_id}/speakers", response_model=SpeakersOut)
async def get_speakers(session_id: uuid.UUID, revision: int | None = None,
                       user_id: uuid.UUID = Depends(current_user)) -> SpeakersOut:
    async with get_engine().connect() as conn:
        await get_owned_session(conn, session_id, user_id)
        return await _speakers(conn, session_id, revision if revision is not None
                               else await _current_revision(conn, session_id))


@router.put("/sessions/{session_id}/speakers", response_model=SpeakersOut)
async def confirm_speakers(body: ConfirmIn, request: Request, session_id: uuid.UUID = Depends(require_consent),
                           user_id: uuid.UUID = Depends(current_user)) -> SpeakersOut:
    """One-tap confirmation or swap. Must name the CURRENT transcript revision and every speaker in it."""
    async with get_engine().begin() as conn:
        await conn.execute(text("SELECT 1 FROM sessions WHERE id = :s FOR UPDATE"), {"s": session_id})
        rev = await _current_revision(conn, session_id)
        if body.transcript_revision != rev:
            raise AppError("invalid_state", f"The transcript changed (current revision is {rev}); reload and confirm again")
        labels = set(await service.labels_for(conn, session_id, rev))
        if set(body.roles) != labels:
            raise AppError("validation_error", f"Give a role for exactly these speakers: {sorted(labels)}")
        before = await _speakers(conn, session_id, rev)
        suggested = {s.speaker_label: s.suggested_role for s in before.speakers}
        await service.confirm(conn, session_id, rev, body.roles, user_id)
        await audit.record(conn, "speaker.confirm", actor_id=user_id, target_type="session", target_id=session_id,
                           ip=client_ip(request), meta={"transcript_revision": rev, "roles": body.roles,
                                                        "suggestion_followed": suggested == body.roles,
                                                        "suggested": suggested})
        await publish(conn, session_id, EventType.SPEAKER_UPDATED, {
            "transcript_revision": rev, "role_assignment": "user_confirmed",
            "mapping": [{"speaker_label": k, "speaker_role": v} for k, v in body.roles.items()]})
        await enqueue_session(conn, session_id, "speaker_confirmed", transcript_revision=rev)  # doc 03 §5 step 5
        return await _speakers(conn, session_id, rev)


@router.patch("/segments/{segment_id}/speaker", response_model=SegmentOverride)
async def set_segment_speaker(segment_id: uuid.UUID, body: SegmentRoleIn, request: Request,
                              user_id: uuid.UUID = Depends(current_user)) -> SegmentOverride:
    """Per-segment correction (doc 03 §5 step 4): speaker_source=user, role_assignment=user_confirmed."""
    async with get_engine().begin() as conn:
        seg = (await conn.execute(text(
            "SELECT g.session_id, g.transcript_revision, g.speaker_label FROM transcript_segments g "
            "JOIN sessions s ON s.id = g.session_id WHERE g.id = :g AND s.owner_id = :u AND s.deleted_at IS NULL"),
            {"g": segment_id, "u": user_id})).first()
        if seg is None:
            raise AppError("not_found", "Segment not found")
        if not await has_active_consent(conn, seg.session_id):
            raise AppError("consent_required", "Active customer consent and recording attestation are required")
        if seg.transcript_revision != await _current_revision(conn, seg.session_id):
            raise AppError("invalid_state", "This segment belongs to an old transcript revision")
        await service.override_segment(conn, seg.session_id, seg.transcript_revision, segment_id, seg.speaker_label,
                                       body.speaker_role, user_id)
        await audit.record(conn, "segment.reassign", actor_id=user_id, target_type="segment",
                           target_id=segment_id, ip=client_ip(request),
                           meta={"session_id": str(seg.session_id), "speaker_role": body.speaker_role})
        await publish(conn, seg.session_id, EventType.SPEAKER_UPDATED, {
            "transcript_revision": seg.transcript_revision, "role_assignment": "user_confirmed",
            "mapping": [{"segment_id": str(segment_id), "speaker_role": body.speaker_role}]})
    return SegmentOverride(segment_id=segment_id, speaker_role=body.speaker_role)
