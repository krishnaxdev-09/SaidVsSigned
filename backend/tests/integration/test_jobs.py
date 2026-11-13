"""Job queue: coalescing dedupe (A7), lease (A8), retry policy (doc 01 §6), cancellation (doc 01 §5.4)."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.core.retry import InputError, SchemaError, TransientError
from app.worker import queue
from app.worker.runner import Registry, Worker
from tests.conftest import create_session, give_consent, register_and_login, scalar

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _kind() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


async def _enqueue(engine, kind, **kw):
    async with engine.begin() as conn:
        return await queue.enqueue(conn, kind, kw.pop("payload", {}), **kw)


async def _status(engine, job_id):
    return await scalar(engine, "SELECT status FROM jobs WHERE id = :i", i=job_id)


async def test_two_workers_never_run_the_same_job(engine):
    kind = _kind()
    runs: list[uuid.UUID] = []
    reg = Registry()

    async def handler(ctx):
        runs.append(ctx.job.id)
        await asyncio.sleep(0.05)

    reg.register(kind, handler)
    ids = [await _enqueue(engine, kind) for _ in range(10)]
    workers = [Worker(engine, reg, lease_seconds=60, worker_id=f"w{i}") for i in range(4)]

    async def drain(w):
        while await w.run_once():
            pass

    await asyncio.gather(*(drain(w) for w in workers))
    assert sorted(runs) == sorted(ids) and len(set(runs)) == 10
    assert all([await _status(engine, i) == "succeeded" for i in ids])


async def test_dedupe_coalesces_queued_and_allows_one_follow_up_during_run(engine):
    kind, key = _kind(), f"k:{uuid.uuid4()}"
    first = await _enqueue(engine, kind, dedupe_key=key)
    assert await _enqueue(engine, kind, dedupe_key=key) is None  # coalesced into queued job
    started, release = asyncio.Event(), asyncio.Event()
    reg = Registry()
    seen: list = []

    async def handler(ctx):
        seen.append(ctx.job.id)
        if ctx.job.id == first:
            started.set()
            await release.wait()

    reg.register(kind, handler)
    w1, w2 = Worker(engine, reg, lease_seconds=60, worker_id="a"), Worker(engine, reg, lease_seconds=60, worker_id="b")
    t = asyncio.create_task(w1.run_once())
    await started.wait()
    follow = await _enqueue(engine, kind, dedupe_key=key)  # trigger during the run -> exactly one follow-up
    assert follow is not None
    assert await _enqueue(engine, kind, dedupe_key=key) is None
    assert await w2.run_once() is False  # claim skips a key that has a running job
    release.set()
    await t
    assert await w2.run_once() is True
    assert seen == [first, follow]


async def test_retry_policy_per_error_class(engine):
    cases = {TransientError("t"): ("queued", "transient"), KeyError("bug"): ("queued", "internal"),
             SchemaError("s"): ("failed", "schema"), InputError("i"): ("failed", "input")}
    for exc, (expected, cls) in cases.items():
        kind = _kind()
        reg = Registry()

        async def handler(ctx, e=exc):
            raise e

        reg.register(kind, handler)
        jid = await _enqueue(engine, kind)
        await Worker(engine, reg, lease_seconds=60).run_once()
        assert await _status(engine, jid) == expected, cls
        assert await scalar(engine, "SELECT error_class FROM jobs WHERE id = :i", i=jid) == cls
        if expected == "queued":
            delay = await scalar(engine, "SELECT extract(epoch FROM run_after - now()) FROM jobs WHERE id = :i", i=jid)
            assert 25 < delay <= 30


async def test_transient_fails_after_five_attempts_and_internal_after_two(engine):
    for exc, limit in ((TransientError("t"), 5), (KeyError("bug"), 2)):
        kind = _kind()
        reg = Registry()

        async def handler(ctx, e=exc):
            raise e

        reg.register(kind, handler)
        jid = await _enqueue(engine, kind)
        w = Worker(engine, reg, lease_seconds=60)
        for _ in range(limit):
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": jid})
            assert await w.run_once()
        assert await _status(engine, jid) == "failed"
        assert await scalar(engine, "SELECT attempts FROM jobs WHERE id = :i", i=jid) == limit


async def test_expired_lease_is_reclaimed_as_transient_attempt(engine):
    kind = _kind()
    jid = await _enqueue(engine, kind)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE jobs SET status='running', attempts=1, locked_by='dead', "
                                "lease_expires_at = now() - interval '1 second' WHERE id = :i"), {"i": jid})
    async with engine.begin() as conn:
        out = await queue.reclaim_expired(conn)
    assert any(j.id == jid and o == "queued" for j, o in out)
    assert await scalar(engine, "SELECT error_class FROM jobs WHERE id = :i", i=jid) == "transient"


async def test_running_job_observes_cancellation_and_commits_nothing(client, engine):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    sid = uuid.UUID(s["id"])
    assert (await give_consent(client, h, s["id"])).status_code == 201
    kind, marker = _kind(), uuid.uuid4().hex
    started, proceed = asyncio.Event(), asyncio.Event()
    reg = Registry()

    async def handler(ctx):
        # a derived write inside the job transaction (uses `events` as a stand-in table)
        await ctx.conn.execute(text("INSERT INTO events (session_id, type, payload) VALUES (:s, 'error', "
                                    "jsonb_build_object('marker', CAST(:m AS text)))"), {"s": sid, "m": marker})
        started.set()
        await proceed.wait()
        await ctx.checkpoint()  # step boundary

    reg.register(kind, handler)
    jid = await _enqueue(engine, kind, session_id=sid)
    task = asyncio.create_task(Worker(engine, reg, lease_seconds=60).run_once())
    await started.wait()
    r = await client.delete(f"/api/v1/sessions/{s['id']}/consents/customer", headers=h)
    assert r.json()["jobs_cancel_requested"] == 1
    proceed.set()
    await task
    assert await _status(engine, jid) == "cancelled"
    assert await scalar(engine, "SELECT count(*) FROM events WHERE payload->>'marker' = :m", m=marker) == 0
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action='job.cancel_observed' AND target_id=:j", j=jid) == 1


async def test_cancellation_between_last_checkpoint_and_commit_commits_nothing(client, engine):
    _, h = await register_and_login(client)
    s = await create_session(client, h)
    sid = uuid.UUID(s["id"])
    await give_consent(client, h, s["id"])
    kind, marker = _kind(), uuid.uuid4().hex
    reg = Registry()

    async def handler(ctx):
        await ctx.conn.execute(text("INSERT INTO events (session_id, type, payload) VALUES (:s, 'error', "
                                    "jsonb_build_object('marker', CAST(:m AS text)))"), {"s": sid, "m": marker})
        # revocation commits after the handler's last own checkpoint
        async with engine.begin() as other:
            await queue.cancel_session_jobs(other, sid)

    reg.register(kind, handler)
    jid = await _enqueue(engine, kind, session_id=sid)
    await Worker(engine, reg, lease_seconds=60).run_once()
    assert await _status(engine, jid) == "cancelled"
    assert await scalar(engine, "SELECT count(*) FROM events WHERE payload->>'marker' = :m", m=marker) == 0
