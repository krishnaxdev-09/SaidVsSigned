"""Append-only audit log (doc 12 §B.4). Ids and metadata only — never content."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# doc 12 §B.4 action list (Phase 0 subset is used now; the rest is reserved for later phases)
ACTIONS = frozenset({
    "auth.register", "auth.login", "auth.logout", "auth.refresh_reuse_detected", "session.create",
    "consent.grant", "attestation.submit", "consent.revoke", "jobs.cancelled", "job.cancel_observed",
    "recording.upload", "recording.replace", "recording.duplicate", "speaker.confirm", "segment.reassign",
    "claim.dismiss", "claim.confirm_quote", "document.upload", "document.page_replace", "evidence.view",
    "report.generate", "report.download", "session.delete_request", "session.erased", "retention.purged",
    "admin.feature_flag_change",
})


async def record(conn: AsyncConnection, action: str, *, actor_id: uuid.UUID | None, target_type: str | None = None,
                 target_id: uuid.UUID | None = None, meta: dict[str, Any] | None = None, ip: str | None = None) -> None:
    if action not in ACTIONS and not action.startswith("admin."):
        raise ValueError(f"unknown audit action {action!r}")
    await conn.execute(text(
        "INSERT INTO audit_log (actor_id, action, target_type, target_id, meta, ip) "
        "VALUES (:a, :act, :tt, :tid, CAST(:m AS jsonb), CAST(:ip AS inet))"),
        {"a": actor_id, "act": action, "tt": target_type, "tid": target_id,
         "m": json.dumps(meta or {}, default=str), "ip": ip})
