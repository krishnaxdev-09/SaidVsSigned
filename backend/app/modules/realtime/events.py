"""Canonical event types (doc 11 §5) and envelope (doc 01 §8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class EventType(StrEnum):
    SESSION_STATUS = "session.status"
    RECORDING_STATUS = "recording.status"
    TRANSCRIPT_READY = "transcript.ready"
    SPEAKER_UPDATED = "speaker.updated"
    CLAIM_CREATED = "claim.created"
    CLAIM_UPDATED = "claim.updated"
    DOCUMENT_STATUS = "document.status"
    VERIFICATION_UPDATED = "verification.updated"
    QA_ANSWER = "qa.answer"
    REPORT_STATUS = "report.status"
    ERROR = "error"
    # Deferred → Later-L1 (live); not produced in the MVP
    TRANSCRIPT_PARTIAL = "transcript.partial"
    TRANSCRIPT_COMMITTED = "transcript.committed"


DEFERRED_EVENT_TYPES = frozenset({EventType.TRANSCRIPT_PARTIAL, EventType.TRANSCRIPT_COMMITTED})


class EventEnvelope(BaseModel):
    type: EventType
    session_id: uuid.UUID
    seq: int
    ts: datetime
    payload: dict[str, Any]
