"""Async database engine (SQLAlchemy 2 core + asyncpg). Repositories use explicit SQL against the doc 11 §3 DDL."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

_engine: AsyncEngine | None = None


def init_engine(url: str) -> AsyncEngine:
    global _engine
    _engine = create_async_engine(url, pool_size=10, max_overflow=10, pool_pre_ping=True)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_conn() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency: one transaction per request; commits on success, rolls back on error."""
    async with get_engine().begin() as conn:
        yield conn
