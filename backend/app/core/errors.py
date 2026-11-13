"""Canonical API error model (doc 01 §7). RFC 7807 application/problem+json."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

log = structlog.get_logger()

# code -> (HTTP status, title). Exactly the doc 01 §7 table (incl. v0.2.2 additions).
ERROR_CODES: dict[str, tuple[int, str]] = {
    "validation_error": (422, "Validation error"),
    "mode_not_available": (422, "Mode not available"),
    "consent_required": (403, "Consent required"),
    "forbidden": (403, "Forbidden"),
    "not_found": (404, "Not found"),
    "conflict_idempotency_key": (409, "Idempotency key reused with a different request"),
    "unsupported_media": (415, "Unsupported media"),
    "too_large": (413, "Too large"),
    "too_long": (413, "Too long"),
    "too_many_pages": (413, "Too many pages"),
    "encrypted_pdf": (400, "Encrypted PDF"),
    "rate_limited": (429, "Rate limited"),
    "provider_unavailable": (503, "Provider unavailable"),
    "internal_error": (500, "Internal error"),
    "unauthorized": (401, "Unauthorized"),
    "idempotency_key_required": (400, "Idempotency-Key header required"),
    "idempotency_in_progress": (409, "Request with this Idempotency-Key is in progress"),
    "invalid_state": (409, "Invalid state"),
    "email_taken": (409, "Email already registered"),
}

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    def __init__(self, code: str, detail: str | None = None, *, headers: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        self.code = code
        self.status, self.title = ERROR_CODES[code]
        self.detail = detail or self.title
        self.headers = headers
        self.extra = extra or {}
        super().__init__(f"{code}: {self.detail}")


def problem(code: str, detail: str | None = None, *, headers: dict[str, str] | None = None,
            extra: dict[str, Any] | None = None) -> JSONResponse:
    status, title = ERROR_CODES[code]
    body = {"type": f"https://saidsigned.local/errors/{code}", "title": title, "status": status,
            "detail": detail or title, "code": code, "request_id": request_id_var.get(), **(extra or {})}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return problem(exc.code, exc.detail, headers=exc.headers, extra=exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return problem("validation_error", "Request validation failed", extra={"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "not_found",
                   413: "too_large", 415: "unsupported_media", 422: "validation_error", 429: "rate_limited"}
        return problem(mapping.get(exc.status_code, "internal_error"), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", error_type=type(exc).__name__)
        return problem("internal_error")
