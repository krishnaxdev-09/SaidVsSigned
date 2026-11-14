"""FastAPI application factory (doc 01 §4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings, check_features, get_settings, load_features
from app.core.db import dispose_engine, get_engine, init_engine
from app.core.errors import install_error_handlers
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.storage import ObjectStore, S3ObjectStore
from app.modules.audio.router import router as audio_router
from app.modules.auth.router import router as auth_router
from app.modules.claims.router import router as claims_router
from app.modules.consent.router import router as consent_router
from app.modules.realtime.bus import hub
from app.modules.realtime.ws import router as ws_router
from app.modules.sessions.router import router as sessions_router
from app.modules.speakers.router import router as speakers_router
from app.modules.stt.router import router as stt_router

log = structlog.get_logger()


def create_app(settings: Settings | None = None, *, store: ObjectStore | None = None) -> FastAPI:
    s = settings or get_settings()
    configure_logging()
    check_features(load_features(s.FEATURES_FILE), real_users=s.REAL_USERS)  # doc 06 §4a startup check
    object_store: ObjectStore = store or S3ObjectStore(s)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        init_engine(s.DATABASE_URL)
        await hub.start(s.asyncpg_dsn)
        log.info("api_start", environment=s.ENVIRONMENT)
        try:
            yield
        finally:
            await hub.stop()
            await dispose_engine()

    app = FastAPI(title="SaidSigned API", version="0.0.1", lifespan=lifespan)
    app.state.store = object_store
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(consent_router)
    app.include_router(ws_router)
    app.include_router(audio_router)
    app.include_router(stt_router)
    app.include_router(speakers_router)
    app.include_router(claims_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["db"] = f"error: {type(exc).__name__}"
        try:
            await object_store.check()
            checks["storage"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["storage"] = f"error: {type(exc).__name__}"
        checks["listener"] = "ok" if hub.connected else "error: disconnected"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(checks, status_code=200 if ok else 503)

    return app
