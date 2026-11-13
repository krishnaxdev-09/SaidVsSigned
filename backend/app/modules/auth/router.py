"""Auth endpoints (doc 11 §4a): register, login, refresh (rotating cookie + reuse detection), logout, ws-ticket."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.deps import client_ip, current_user
from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.security import create_access_token, hash_password, new_opaque_token, token_hash, verify_password
from app.modules.audit import service as audit
from app.modules.sessions.service import get_owned_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
REFRESH_COOKIE = "ss_refresh"
COOKIE_PATH = "/api/v1/auth"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


class RegisterOut(BaseModel):
    user_id: uuid.UUID
    email: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class WsTicketIn(BaseModel):
    session_id: uuid.UUID
    purpose: Literal["events"]  # 'audio' deferred to Later-L1


class WsTicketOut(BaseModel):
    ticket: str
    expires_at: datetime


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(REFRESH_COOKIE, token, max_age=s.JWT_REFRESH_TTL, httponly=True, secure=s.COOKIE_SECURE,
                        samesite="strict", path=COOKIE_PATH)


async def _issue_tokens(conn: AsyncConnection, response: Response, user_id: uuid.UUID,
                        family_id: uuid.UUID | None = None) -> tuple[TokenOut, uuid.UUID]:
    s = get_settings()
    refresh = new_opaque_token()
    rid = uuid7()
    await conn.execute(text(
        "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, expires_at) "
        "VALUES (:id, :u, :f, :h, now() + make_interval(secs => :ttl))"),
        {"id": rid, "u": user_id, "f": family_id or uuid7(), "h": token_hash(refresh), "ttl": s.JWT_REFRESH_TTL})
    _set_refresh_cookie(response, refresh)
    access = create_access_token(user_id, s.JWT_SECRET.get_secret_value(), s.JWT_ACCESS_TTL)
    return TokenOut(access_token=access, expires_in=s.JWT_ACCESS_TTL), rid


@router.post("/register", status_code=201, response_model=RegisterOut)
async def register(body: RegisterIn, request: Request) -> RegisterOut:
    uid = uuid7()
    async with get_engine().begin() as conn:
        created = (await conn.execute(text(
            "INSERT INTO users (id, email, password_hash) VALUES (:id, :e, :p) ON CONFLICT (email) DO NOTHING "
            "RETURNING id"), {"id": uid, "e": body.email, "p": hash_password(body.password)})).first()
        if not created:
            raise AppError("email_taken")
        await audit.record(conn, "auth.register", actor_id=uid, target_type="user", target_id=uid, ip=client_ip(request))
    return RegisterOut(user_id=uid, email=body.email)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, response: Response) -> TokenOut:
    async with get_engine().begin() as conn:
        row = (await conn.execute(text(
            "SELECT id, password_hash FROM users WHERE email = :e AND deleted_at IS NULL"), {"e": body.email})).first()
        if not row or not verify_password(row.password_hash, body.password):
            raise AppError("unauthorized", "Invalid email or password")
        out, _ = await _issue_tokens(conn, response, row.id)
        await audit.record(conn, "auth.login", actor_id=row.id, target_type="user", target_id=row.id,
                           ip=client_ip(request))
    return out


@router.post("/refresh", response_model=TokenOut)
async def refresh(request: Request, response: Response) -> TokenOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AppError("unauthorized", "Missing refresh cookie")
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT id, user_id, family_id, expires_at, revoked_at FROM refresh_tokens WHERE token_hash = :h FOR UPDATE"),
            {"h": token_hash(token)})).first()
        if row is not None and row.revoked_at is None and row.expires_at > datetime.now(UTC):
            out, new_id = await _issue_tokens(conn, response, row.user_id, row.family_id)
            await conn.execute(text("UPDATE refresh_tokens SET revoked_at = now(), replaced_by = :n WHERE id = :id"),
                               {"n": new_id, "id": row.id})
            return out
    if row is not None and row.revoked_at is not None:
        # Reuse of a rotated/revoked token: revoke the whole family (doc 11 §3, A12)
        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = :f AND revoked_at IS NULL"),
                {"f": row.family_id})
            await audit.record(conn, "auth.refresh_reuse_detected", actor_id=row.user_id, target_type="user",
                               target_id=row.user_id, meta={"family_id": str(row.family_id)}, ip=client_ip(request))
    raise AppError("unauthorized", "Invalid refresh token")


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        async with get_engine().begin() as conn:
            row = (await conn.execute(text(
                "UPDATE refresh_tokens SET revoked_at = coalesce(revoked_at, now()) WHERE token_hash = :h "
                "RETURNING user_id"), {"h": token_hash(token)})).first()
            if row:
                await audit.record(conn, "auth.logout", actor_id=row.user_id, target_type="user",
                                   target_id=row.user_id, ip=client_ip(request))
    response.status_code = 204
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    return response


@router.post("/ws-ticket", response_model=WsTicketOut)
async def ws_ticket(body: WsTicketIn, user_id: uuid.UUID = Depends(current_user)) -> WsTicketOut:
    s = get_settings()
    ticket = new_opaque_token()
    expires = datetime.now(UTC) + timedelta(seconds=s.WS_TICKET_TTL)
    async with get_engine().begin() as conn:
        await get_owned_session(conn, body.session_id, user_id)
        await conn.execute(text(
            "INSERT INTO ws_tickets (ticket_hash, user_id, session_id, purpose, expires_at) "
            "VALUES (:h, :u, :s, :p, :e)"),
            {"h": token_hash(ticket), "u": user_id, "s": body.session_id, "p": body.purpose, "e": expires})
    return WsTicketOut(ticket=ticket, expires_at=expires)
