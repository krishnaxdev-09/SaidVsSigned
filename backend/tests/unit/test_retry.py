"""Doc 01 §6 canonical retry policy: all four error classes."""

from datetime import timedelta

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.core.retry import (
    ErrorClass,
    InputError,
    SchemaError,
    TransientError,
    call_with_retries,
    call_with_schema_retry,
    classify,
    next_job_delay,
)


class M(BaseModel):
    x: int


def _status_error(code: int, headers=None) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.test")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req, headers=headers or {}))


def test_classify():
    assert classify(TransientError()) is ErrorClass.TRANSIENT
    assert classify(TimeoutError()) is ErrorClass.TRANSIENT
    assert classify(_status_error(503)) is ErrorClass.TRANSIENT
    assert classify(_status_error(429)) is ErrorClass.TRANSIENT
    assert classify(_status_error(400)) is ErrorClass.INPUT
    assert classify(InputError("bad file")) is ErrorClass.INPUT
    assert classify(SchemaError()) is ErrorClass.SCHEMA
    with pytest.raises(ValidationError) as ei:
        M.model_validate({"x": "nope"})
    assert classify(ei.value) is ErrorClass.SCHEMA
    assert classify(KeyError("boom")) is ErrorClass.INTERNAL


def test_job_level_policy():
    # transient: up to 5 attempts with 30 s, 2 m, 10 m, 30 m
    assert [next_job_delay(ErrorClass.TRANSIENT, n) for n in range(1, 6)] == [
        timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=30), None]
    # schema and input: never retried at job level
    assert next_job_delay(ErrorClass.SCHEMA, 1) is None
    assert next_job_delay(ErrorClass.INPUT, 1) is None
    # internal: 2 attempts
    assert next_job_delay(ErrorClass.INTERNAL, 1) == timedelta(seconds=30)
    assert next_job_delay(ErrorClass.INTERNAL, 2) is None


async def test_in_call_transient_retries_twice_with_backoff():
    sleeps: list[float] = []
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise TransientError()

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    with pytest.raises(TransientError):
        await call_with_retries(fn, sleep=fake_sleep, jitter=lambda: 0.0)
    assert calls == 3 and sleeps == [1.0, 4.0]


async def test_in_call_honours_retry_after():
    sleeps: list[float] = []
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, {"retry-after": "7"})
        return "ok"

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    assert await call_with_retries(fn, sleep=fake_sleep, jitter=lambda: 0.0) == "ok"
    assert sleeps == [7.0]


async def test_input_and_internal_not_retried_in_call():
    for exc in (InputError("bad"), KeyError("bug"), SchemaError()):
        calls = 0

        async def fn(e=exc):
            nonlocal calls
            calls += 1
            raise e

        with pytest.raises(type(exc)):
            await call_with_retries(fn, sleep=lambda s: None)  # type: ignore[arg-type]
        assert calls == 1


async def test_schema_gets_exactly_one_corrective_retry():
    seen: list = []

    async def fn(errors):
        seen.append(errors)
        raise SchemaError("still invalid")

    with pytest.raises(SchemaError):
        await call_with_schema_retry(fn)
    assert len(seen) == 2 and seen[0] is None and "still invalid" in seen[1][0]
