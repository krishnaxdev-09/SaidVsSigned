"""Structured JSON logging with request ids. Never log bodies, transcripts or document text (doc 12 §B.2)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REDACT_KEYS = {"password", "token", "access_token", "refresh_token", "ticket", "authorization", "cookie",
                "jwt_secret", "s3_secret_key", "elevenlabs_api_key", "body"}


def _redact(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k in list(event):
        if k.lower() in _REDACT_KEYS:
            event[k] = "[redacted]"
    rid = request_id_var.get()
    if rid:
        event.setdefault("request_id", rid)
    return event


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"),
                    _redact, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response
