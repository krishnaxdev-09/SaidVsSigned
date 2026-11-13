"""Password hashing (argon2id), access JWTs, opaque refresh tokens and WS tickets (doc 01 §4, doc 11 §4a, doc 12 §B.2)."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.errors import AppError

_ph = PasswordHasher()  # argon2id defaults


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: uuid.UUID, secret: str, ttl_s: int, *, now: float | None = None) -> str:
    iat = int(now if now is not None else time.time())
    payload: dict[str, Any] = {"sub": str(user_id), "iat": iat, "exp": iat + ttl_s, "typ": "access"}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "typ"]})
    except jwt.PyJWTError as exc:
        raise AppError("unauthorized", "Invalid or expired access token") from exc
    if payload.get("typ") != "access":
        raise AppError("unauthorized", "Wrong token type")
    return uuid.UUID(payload["sub"])


def new_opaque_token() -> str:
    """Refresh tokens and WS tickets: 32 random bytes, stored only as sha256."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
