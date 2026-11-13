"""Postgres job queue (doc 01 §4 step 8, §5.4, §6; doc 11 §2–3, v0.2.2 A7/A8).

- Coalescing dedupe: unique `dedupe_key` among *queued* jobs; claim skips a key that has a *running* job.
  => at most one queued + one running per key; a trigger during a run yields exactly one follow-up.
- Lease: running jobs carry `lease_expires_at`; expired leases are reclaimed as a transient failed attempt.
- Retry: doc 01 §6 via app.core.retry.next_job_delay.
- Cancellation: revocation cancels queued jobs and sets cancel_requested_at on running ones; enqueue is refused
  for sessions whose customer consent was revoked.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.retry import ErrorClass, next_job_delay


class EnqueueRefused(AppError):
    def __init__(self) -> None:
        super().__init__("consent_required", "Customer consent was revoked; no new processing is allowed")


@dataclass
class Job:
    id: uuid.UUID
    session_id: uuid.UUID | None
    kind: str
    dedupe_key: str | None
    payload: dict[str, Any]
    attempts: int
    lease_expires_at: datetime | None


async def enqueue(conn: AsyncConnection, kind: str, payload: dict[str, Any], *, session_id: uuid.UUID | None = None,
                  dedupe_key: str | None = None, delay_seconds: float = 0) -> uuid.UUID | None:
    """Returns the new job id, or None when coalesced into an already-queued job with the same key."""
    if session_id is not None:
        revoked = (await conn.execute(text(
            "SELECT 1 FROM sessions WHERE id = :s AND state = 'failed' AND state_reason = 'consent_revoked'"),
            {"s": session_id})).first()
        if revoked:
            raise EnqueueRefused()
    row = (await conn.execute(text(
        "INSERT INTO jobs (id, session_id, kind, dedupe_key, payload, status, run_after) "
        "VALUES (:id, :s, :k, :d, CAST(:p AS jsonb), 'queued', now() + make_interval(secs => :delay)) "
        "ON CONFLICT (dedupe_key) WHERE status = 'queued' DO NOTHING RETURNING id"),
        {"id": uuid7(), "s": session_id, "k": kind, "d": dedupe_key, "p": json.dumps(payload, default=str),
         "delay": delay_seconds})).first()
    return row.id if row else None


_CLAIM_SQL = """
UPDATE jobs SET status = 'running', locked_by = :w, locked_at = now(), attempts = attempts + 1,
       lease_expires_at = now() + make_interval(secs => :lease)
WHERE id = (
  SELECT j.id FROM jobs j
  WHERE j.status = 'queued' AND j.run_after <= now() AND j.kind = ANY(:kinds)
    AND (j.dedupe_key IS NULL OR NOT EXISTS (
         SELECT 1 FROM jobs r WHERE r.dedupe_key = j.dedupe_key AND r.status = 'running'))
  ORDER BY j.run_after, j.created_at
  FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, session_id, kind, dedupe_key, payload, attempts, lease_expires_at
"""


async def claim(conn: AsyncConnection, worker_id: str, kinds: list[str], lease_seconds: int) -> Job | None:
    # Claims are serialised by one transaction-scoped advisory lock, so the "no running job with this key" check and
    # the queued->running transition are atomic across workers. Claims are cheap; MVP throughput is fine.
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('saidsigned.jobs.claim'))"))
    row = (await conn.execute(text(_CLAIM_SQL), {"w": worker_id, "kinds": kinds, "lease": lease_seconds})).mappings().first()
    return Job(**row) if row else None


async def heartbeat(conn: AsyncConnection, job_id: uuid.UUID, lease_seconds: int) -> None:
    await conn.execute(text(
        "UPDATE jobs SET lease_expires_at = now() + make_interval(secs => :l) WHERE id = :id AND status = 'running'"),
        {"l": lease_seconds, "id": job_id})


async def cancel_requested(conn: AsyncConnection, job_id: uuid.UUID) -> bool:
    row = (await conn.execute(text("SELECT cancel_requested_at FROM jobs WHERE id = :id"), {"id": job_id})).first()
    return bool(row and row.cancel_requested_at)


async def mark_succeeded(conn: AsyncConnection, job_id: uuid.UUID) -> None:
    await conn.execute(text(
        "UPDATE jobs SET status = 'succeeded', finished_at = now(), lease_expires_at = NULL WHERE id = :id"),
        {"id": job_id})


async def mark_cancelled(conn: AsyncConnection, job_id: uuid.UUID, reason: str) -> None:
    await conn.execute(text(
        "UPDATE jobs SET status = 'cancelled', finished_at = now(), lease_expires_at = NULL, last_error = :r "
        "WHERE id = :id"), {"r": reason, "id": job_id})


async def _requeue_or_coalesce(conn: AsyncConnection, job_id: uuid.UUID, delay_s: float, error_class: ErrorClass,
                               err: str) -> str:
    """Move a job back to queued. If a queued job with the same key already exists, the work is coalesced into it."""
    await conn.execute(text("SAVEPOINT requeue"))
    try:
        await conn.execute(text(
            "UPDATE jobs SET status = 'queued', run_after = now() + make_interval(secs => :d), locked_by = NULL, "
            "lease_expires_at = NULL, error_class = :c, last_error = :e WHERE id = :id"),
            {"d": delay_s, "c": error_class.value, "e": err, "id": job_id})
        await conn.execute(text("RELEASE SAVEPOINT requeue"))
        return "queued"
    except Exception as exc:  # unique violation on jobs_dedupe_queued
        if "jobs_dedupe_queued" not in str(exc):
            raise
        await conn.execute(text("ROLLBACK TO SAVEPOINT requeue"))
        await mark_cancelled(conn, job_id, f"{err}; coalesced into an already-queued job with the same dedupe_key")
        return "coalesced"


async def mark_failed_attempt(conn: AsyncConnection, job: Job, error_class: ErrorClass, err: str) -> str:
    """Apply doc 01 §6. Returns 'queued', 'coalesced' or 'failed'."""
    redacted = err[:500]
    delay = next_job_delay(error_class, job.attempts)
    if delay is None:
        await conn.execute(text(
            "UPDATE jobs SET status = 'failed', finished_at = now(), lease_expires_at = NULL, error_class = :c, "
            "last_error = :e WHERE id = :id"), {"c": error_class.value, "e": redacted, "id": job.id})
        return "failed"
    return await _requeue_or_coalesce(conn, job.id, delay.total_seconds(), error_class, redacted)


async def reclaim_expired(conn: AsyncConnection) -> list[tuple[Job, str]]:
    """Expired leases count as a transient failed attempt (A8)."""
    rows = (await conn.execute(text(
        "SELECT id, session_id, kind, dedupe_key, payload, attempts, lease_expires_at FROM jobs "
        "WHERE status = 'running' AND lease_expires_at < now() FOR UPDATE SKIP LOCKED"))).mappings().all()
    out = []
    for r in rows:
        job = Job(**r)
        out.append((job, await mark_failed_attempt(conn, job, ErrorClass.TRANSIENT, "lease expired")))
    return out


async def cancel_session_jobs(conn: AsyncConnection, session_id: uuid.UUID) -> tuple[int, int]:
    """Consent revocation (doc 01 §5.4): queued -> cancelled; running -> cancel_requested_at."""
    cancelled = (await conn.execute(text(
        "UPDATE jobs SET status = 'cancelled', finished_at = now(), last_error = 'consent_revoked' "
        "WHERE session_id = :s AND status = 'queued'"), {"s": session_id})).rowcount or 0
    requested = (await conn.execute(text(
        "UPDATE jobs SET cancel_requested_at = now() WHERE session_id = :s AND status = 'running'"),
        {"s": session_id})).rowcount or 0
    return cancelled, requested
