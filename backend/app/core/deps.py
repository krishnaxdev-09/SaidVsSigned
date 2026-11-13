"""Shared request dependencies: authenticated user, client ip, idempotent-create helper."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core import idempotency
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import AppError
from app.core.security import decode_access_token


async def current_user(request: Request) -> uuid.UUID:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AppError("unauthorized", "Missing bearer token")
    return decode_access_token(auth[7:].strip(), get_settings().JWT_SECRET.get_secret_value())


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def idempotent_create(request: Request, user_id: uuid.UUID, status_code: int,
                            handler: Callable[[], Awaitable[Any]]) -> JSONResponse:
    """Wrap a resource-creating handler with doc 11 §2 idempotency. Only 2xx responses are stored."""
    engine = get_engine()
    body = await request.body()
    outcome = await idempotency.begin(engine, user_id, request.headers.get("idempotency-key"), request.method,
                                      request.url.path, body)
    if isinstance(outcome, idempotency.Replay):
        return JSONResponse(outcome.body, status_code=outcome.status_code, headers={"Idempotent-Replay": "true"})
    try:
        result = jsonable_encoder(await handler())
    except BaseException:
        await idempotency.abandon(engine, outcome)
        raise
    await idempotency.complete(engine, outcome, status_code, result)
    return JSONResponse(result, status_code=status_code)
