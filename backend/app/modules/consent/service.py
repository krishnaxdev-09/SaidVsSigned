"""Customer consent + recording attestation (canonical: doc 01 §5; models: doc 11 §4a).

The attestation is the CUSTOMER'S SELF-DECLARATION. It is not independent proof that the agent consented or was
informed, and SaidSigned does not verify it. All rendered wording comes from `attestation_note()`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

AgentInformed = Literal["yes", "no", "unsure"]
RecordingSource = Literal["phone_recorder", "call_recorder", "other"]

NOT_VERIFIED_SUFFIX = "(not independently verified)"
_NOTES: dict[str, str] = {
    "yes": "The customer states the agent was informed of the recording " + NOT_VERIFIED_SUFFIX + ".",
    "no": "The customer states the agent was not informed of the recording " + NOT_VERIFIED_SUFFIX + ".",
    "unsure": "The customer states the agent may not have been informed of the recording " + NOT_VERIFIED_SUFFIX + ".",
}
LEGAL_CAUTION = "Recording someone without their knowledge may have legal implications."


def attestation_note(agent_informed: AgentInformed) -> str:
    """Canonical customer-statement wording (doc 01 §5.3). Never phrased as agent consent."""
    return _NOTES[agent_informed]


def sha256_text(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Attestation(BaseModel):
    i_was_party: bool
    agent_informed: AgentInformed
    recorded_on: date | None = None
    recording_source: RecordingSource | None = None
    attested_at: datetime | None = None


class ConsentStatus(BaseModel):
    active: bool
    consent_id: uuid.UUID | None = None
    consent_text_version: str | None = None
    granted_at: datetime | None = None
    revoked_at: datetime | None = None
    attestation: Attestation | None = None
    attestation_note: str | None = None
    legal_caution: str | None = None


async def consent_status(conn: AsyncConnection, session_id: uuid.UUID) -> ConsentStatus:
    """Latest customer consent for the session (active one if present)."""
    row = (await conn.execute(text(
        "SELECT id, consent_text_version, granted_at, revoked_at, attestation, attested_at FROM consents "
        "WHERE session_id = :s AND party = 'customer' ORDER BY (revoked_at IS NULL) DESC, granted_at DESC LIMIT 1"),
        {"s": session_id})).mappings().first()
    if row is None:
        return ConsentStatus(active=False)
    att = None
    note = caution = None
    if row["attestation"]:
        att = Attestation(**row["attestation"], attested_at=row["attested_at"])
        note = attestation_note(att.agent_informed)
        caution = LEGAL_CAUTION if att.agent_informed != "yes" else None
    return ConsentStatus(active=row["revoked_at"] is None and att is not None, consent_id=row["id"],
                         consent_text_version=row["consent_text_version"], granted_at=row["granted_at"],
                         revoked_at=row["revoked_at"], attestation=att, attestation_note=note, legal_caution=caution)


async def has_active_consent(conn: AsyncConnection, session_id: uuid.UUID) -> bool:
    row = (await conn.execute(text(
        "SELECT 1 FROM consents WHERE session_id = :s AND party = 'customer' AND revoked_at IS NULL "
        "AND attestation IS NOT NULL AND (attestation->>'i_was_party')::boolean IS TRUE"), {"s": session_id})).first()
    return row is not None


def attestation_json(att: Attestation) -> str:
    return json.dumps(att.model_dump(exclude={"attested_at"}, mode="json"))
