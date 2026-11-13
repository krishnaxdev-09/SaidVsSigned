"""Canonical retry policy (doc 01 §6).

| class     | in-call retry                    | job-level                              |
|-----------|----------------------------------|----------------------------------------|
| transient | 2 retries, 1 s -> 4 s (+jitter)  | up to 5 attempts: 30 s, 2 m, 10 m, 30 m |
| schema    | 1 corrective retry               | none                                   |
| input     | none                             | none                                   |
| internal  | none                             | 2 attempts                             |
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TypeVar

import httpx
from pydantic import ValidationError

T = TypeVar("T")


class ErrorClass(StrEnum):
    TRANSIENT = "transient"
    SCHEMA = "schema"
    INPUT = "input"
    INTERNAL = "internal"


class TransientError(Exception):
    def __init__(self, msg: str = "transient error", retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class SchemaError(Exception):
    """LLM/provider output failed schema or validator checks."""


class InputError(Exception):
    """Bad user input (unsupported/corrupt file, over limits). Never retried."""

    def __init__(self, msg: str, code: str = "unsupported_media") -> None:
        super().__init__(msg)
        self.code = code


def classify(exc: BaseException) -> ErrorClass:
    if isinstance(exc, InputError):
        return ErrorClass.INPUT
    if isinstance(exc, SchemaError | ValidationError):
        return ErrorClass.SCHEMA
    if isinstance(exc, TransientError | TimeoutError | ConnectionError | httpx.TransportError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429 or code >= 500:
            return ErrorClass.TRANSIENT
        return ErrorClass.INPUT
    return ErrorClass.INTERNAL


@dataclass(frozen=True)
class JobRetryRule:
    max_attempts: int
    delays: tuple[timedelta, ...]


JOB_RETRY: dict[ErrorClass, JobRetryRule] = {
    ErrorClass.TRANSIENT: JobRetryRule(5, (timedelta(seconds=30), timedelta(minutes=2),
                                           timedelta(minutes=10), timedelta(minutes=30))),
    ErrorClass.SCHEMA: JobRetryRule(1, ()),
    ErrorClass.INPUT: JobRetryRule(1, ()),
    ErrorClass.INTERNAL: JobRetryRule(2, (timedelta(seconds=30),)),
}


def next_job_delay(error_class: ErrorClass, attempts_done: int) -> timedelta | None:
    """Delay before the next attempt, or None if the job must fail. `attempts_done` counts the failed attempt."""
    rule = JOB_RETRY[error_class]
    if attempts_done >= rule.max_attempts:
        return None
    return rule.delays[min(attempts_done - 1, len(rule.delays) - 1)]


IN_CALL_BACKOFF = (1.0, 4.0)


def _retry_after(exc: BaseException) -> float | None:
    if isinstance(exc, TransientError):
        return exc.retry_after
    if isinstance(exc, httpx.HTTPStatusError):
        value = exc.response.headers.get("retry-after")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None
    return None


async def call_with_retries(fn: Callable[[], Awaitable[T]], *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                            jitter: Callable[[], float] = lambda: random.uniform(0, 0.25)) -> T:  # noqa: S311
    """In-call retry for provider calls: transient errors get 2 retries (1 s, 4 s + jitter, honouring Retry-After)."""
    for attempt in range(len(IN_CALL_BACKOFF) + 1):
        try:
            return await fn()
        except BaseException as exc:
            if classify(exc) is not ErrorClass.TRANSIENT or attempt == len(IN_CALL_BACKOFF):
                raise
            await sleep(max(IN_CALL_BACKOFF[attempt] + jitter(), _retry_after(exc) or 0.0))
    raise AssertionError("unreachable")


async def call_with_schema_retry(fn: Callable[[list[str] | None], Awaitable[T]]) -> T:
    """Schema class: one corrective retry with the validation errors passed back to the caller's prompt builder."""
    try:
        return await fn(None)
    except (SchemaError, ValidationError) as exc:
        return await fn([str(exc)])
