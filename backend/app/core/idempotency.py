"""HTTP idempotency (canonical: doc 11 §2, v0.2.2 A2).

- request_hash = sha256(method + path + canonical JSON body)
- same key + same hash -> replay stored 2xx response
- same key + different hash -> 409 conflict_idempotency_key
- missing header -> 400 idempotency_key_required
- in flight (< 60 s, no status) -> 409 idempotency_in_progress, Retry-After: 2
- only 2xx responses are stored; on failure the key row is deleted so the client can retry.
The key row lives in its own short transactions, separate from the business transaction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import AppError

IN_FLIGHT_SECONDS = 60


def canonical_hash(method: str, path: str, body: bytes) -> str:
    try:
        canon = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")) if body else ""
    except ValueError:
        canon = body.decode(errors="replace")
    return hashlib.sha256(f"{method.upper()}\n{path}\n{canon}".encode()).hexdigest()


@dataclass
class Replay:
    status_code: int
    body: Any


@dataclass
class Claim:
    user_id: uuid.UUID
    key: str


async def begin(engine: AsyncEngine, user_id: uuid.UUID, key: str | None, method: str, path: str,
                body: bytes) -> Replay | Claim:
    if not key:
        raise AppError("idempotency_key_required")
    if len(key) > 200:
        raise AppError("validation_error", "Idempotency-Key too long")
    h = canonical_hash(method, path, body)
    async with engine.begin() as conn:
        inserted = (await conn.execute(text(
            "INSERT INTO idempotency_keys (user_id, key, method, path, request_hash) VALUES (:u, :k, :m, :p, :h) "
            "ON CONFLICT (user_id, key) DO NOTHING RETURNING key"),
            {"u": user_id, "k": key, "m": method.upper(), "p": path, "h": h})).first()
        if inserted:
            return Claim(user_id, key)
        row = (await conn.execute(text(
            "SELECT request_hash, status_code, response_body, created_at > now() - make_interval(secs => :s) AS fresh "
            "FROM idempotency_keys WHERE user_id = :u AND key = :k FOR UPDATE"),
            {"u": user_id, "k": key, "s": IN_FLIGHT_SECONDS})).mappings().one()
        if row["request_hash"] != h:
            raise AppError("conflict_idempotency_key")
        if row["status_code"] is not None:
            return Replay(row["status_code"], row["response_body"])
        if row["fresh"]:
            raise AppError("idempotency_in_progress", headers={"Retry-After": "2"})
        # abandoned in-flight request: take it over
        await conn.execute(text("UPDATE idempotency_keys SET created_at = now() WHERE user_id = :u AND key = :k"),
                           {"u": user_id, "k": key})
        return Claim(user_id, key)


async def complete(engine: AsyncEngine, claim: Claim, status_code: int, body: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE idempotency_keys SET status_code = :s, response_body = CAST(:b AS jsonb) "
            "WHERE user_id = :u AND key = :k"),
            {"s": status_code, "b": json.dumps(body, default=str), "u": claim.user_id, "k": claim.key})


async def abandon(engine: AsyncEngine, claim: Claim) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM idempotency_keys WHERE user_id = :u AND key = :k AND status_code IS NULL"),
                           {"u": claim.user_id, "k": claim.key})


async def purge_expired(engine: AsyncEngine, ttl_hours: int = 24) -> int:
    async with engine.begin() as conn:
        res = await conn.execute(text(
            "DELETE FROM idempotency_keys WHERE created_at < now() - make_interval(hours => :h)"), {"h": ttl_hours})
        return res.rowcount or 0
