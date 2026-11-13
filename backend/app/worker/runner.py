"""Worker runner (doc 01 §4 step 8, §5.4, §6).

Each handler runs inside ONE transaction together with its success marker, so a cancelled, failed or lease-lost
job commits nothing. Handlers must call `ctx.checkpoint()` at step boundaries (before provider calls, before each
write batch, before publishing events) to observe cancellation (doc 01 §5.4).

Phase 0 registers no production job kinds; Phase 1+ add handlers via `registry.register`.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.retry import classify
from app.modules.audit import service as audit
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.worker import queue

log = structlog.get_logger()


class JobCancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


@dataclass
class JobContext:
    job: queue.Job
    conn: AsyncConnection
    engine: AsyncEngine
    lease_seconds: int

    async def checkpoint(self) -> None:
        """Step boundary: abort if cancellation was requested; renew the lease (on a separate connection)."""
        async with self.engine.begin() as c:
            if await queue.cancel_requested(c, self.job.id):
                raise JobCancelled()
            await queue.heartbeat(c, self.job.id, self.lease_seconds)


Handler = Callable[[JobContext], Awaitable[None]]


class Registry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, kind: str, handler: Handler) -> None:
        if kind in self._handlers:
            raise ValueError(f"handler for {kind!r} already registered")
        self._handlers[kind] = handler

    def get(self, kind: str) -> Handler:
        return self._handlers[kind]

    @property
    def kinds(self) -> list[str]:
        return list(self._handlers)


registry = Registry()


class Worker:
    def __init__(self, engine: AsyncEngine, reg: Registry, *, lease_seconds: int, worker_id: str | None = None) -> None:
        self.engine = engine
        self.registry = reg
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    async def run_once(self) -> bool:
        """Claim and run at most one job. Returns True if a job was processed."""
        async with self.engine.begin() as conn:
            await queue.reclaim_expired(conn)
            if not self.registry.kinds:
                return False
            job = await queue.claim(conn, self.worker_id, self.registry.kinds, self.lease_seconds)
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job: queue.Job) -> None:
        handler = self.registry.get(job.kind)
        try:
            async with self.engine.begin() as conn:
                ctx = JobContext(job, conn, self.engine, self.lease_seconds)
                await ctx.checkpoint()
                await handler(ctx)
                await ctx.checkpoint()  # final boundary before commit
                # Atomic with the handler's writes. Row lock serialises against revocation (doc 01 §5.4):
                # if revocation committed first, cancel_requested_at is set and nothing is committed.
                done = (await conn.execute(text(
                    "UPDATE jobs SET status = 'succeeded', finished_at = now(), lease_expires_at = NULL "
                    "WHERE id = :id AND status = 'running' AND locked_by = :w AND cancel_requested_at IS NULL"),
                    {"id": job.id, "w": self.worker_id})).rowcount
                if not done:
                    state = (await conn.execute(text("SELECT cancel_requested_at FROM jobs WHERE id = :id"),
                                                {"id": job.id})).first()
                    if state and state.cancel_requested_at:
                        raise JobCancelled()
                    raise LeaseLost()
        except JobCancelled:
            async with self.engine.begin() as conn:
                await queue.mark_cancelled(conn, job.id, "consent_revoked")
                await audit.record(conn, "job.cancel_observed", actor_id=None, target_type="job", target_id=job.id,
                                   meta={"kind": job.kind, "session_id": str(job.session_id)})
            log.info("job_cancelled", job_id=str(job.id), kind=job.kind)
        except LeaseLost:
            log.warning("job_lease_lost", job_id=str(job.id), kind=job.kind)
        except Exception as exc:  # noqa: BLE001
            error_class = classify(exc)
            async with self.engine.begin() as conn:
                outcome = await queue.mark_failed_attempt(conn, job, error_class, f"{type(exc).__name__}: {exc}")
                if outcome == "failed" and job.session_id is not None:
                    await publish(conn, job.session_id, EventType.ERROR,
                                  {"code": "job_failed", "message": "Processing failed",
                                   "retryable": error_class.value == "transient", "job_kind": job.kind})
            log.warning("job_attempt_failed", job_id=str(job.id), kind=job.kind, error_class=error_class.value,
                        outcome=outcome)

    async def run_forever(self, poll_seconds: float) -> None:
        from app.core import idempotency

        last_purge = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                worked = await self.run_once()
                if loop.time() - last_purge > 3600:
                    await idempotency.purge_expired(self.engine)
                    last_purge = loop.time()
            except Exception as exc:  # noqa: BLE001
                log.error("worker_loop_error", error_type=type(exc).__name__)
                worked = False
            if not worked:
                await asyncio.sleep(poll_seconds)


async def main() -> None:
    from app.core.config import get_settings
    from app.core.db import init_engine
    from app.core.logging import configure_logging

    configure_logging()
    s = get_settings()
    engine = init_engine(s.DATABASE_URL)
    log.info("worker_start", kinds=registry.kinds)
    await Worker(engine, registry, lease_seconds=s.JOB_LEASE_SECONDS).run_forever(s.WORKER_POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
