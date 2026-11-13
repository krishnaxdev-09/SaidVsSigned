"""Test configuration. Unit tests need nothing external. Integration tests need Postgres from infra/docker-compose.yml.

Integration tests run the real app under uvicorn (own thread + loop) against a dedicated `saidsigned_test` database,
and talk to it over real HTTP/WebSocket. Test code uses its own SQLAlchemy engine for assertions.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def _load_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


_DOT = _load_dotenv()
BASE_URL = os.environ.get("DATABASE_URL", _DOT.get("DATABASE_URL", ""))
TEST_DB = "saidsigned_test"
TEST_URL = BASE_URL.rsplit("/", 1)[0] + "/" + TEST_DB if BASE_URL else ""

# Settings for code under test (set before app modules read get_settings()).
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ["ENVIRONMENT"] = "test"
os.environ["COOKIE_SECURE"] = "false"
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL


def _db_available() -> bool:
    if not BASE_URL:
        return False
    host_port = BASE_URL.split("@", 1)[1].split("/", 1)[0]
    host, _, port = host_port.partition(":")
    try:
        with socket.create_connection((host, int(port or 5432)), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _db_available():
        return
    skip = pytest.mark.skip(reason="Postgres not reachable (start infra/docker-compose.yml db)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def _admin_sql(sql: str) -> None:
    import psycopg

    dsn = BASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)


def _alembic(*args: str, url: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, check=True,
                   env={**os.environ, "DATABASE_URL": url}, capture_output=True)


@pytest.fixture(scope="session")
def test_database() -> str:
    _admin_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    _admin_sql(f"CREATE DATABASE {TEST_DB}")
    _alembic("upgrade", "head", url=TEST_URL)
    return TEST_URL


@pytest.fixture(scope="session")
async def engine(test_database: str):  # type: ignore[no-untyped-def]
    eng = create_async_engine(test_database, pool_size=5)
    from app.cli import seed_consent_texts

    await seed_consent_texts(eng)
    yield eng
    await eng.dispose()


class FakeStore:
    ok = True

    async def check(self) -> bool:
        if not self.ok:
            raise ConnectionError("storage down")
        return True


@pytest.fixture(scope="session")
def fake_store() -> FakeStore:
    return FakeStore()


@pytest.fixture(scope="session")
def server(engine, fake_store):  # type: ignore[no-untyped-def]
    """Run the real app with uvicorn in a background thread. Mounts a TEST-ONLY consent-gated route."""
    import uvicorn
    from fastapi import Depends

    from app.core.config import get_settings
    from app.main import create_app
    from app.modules.consent.gate import require_consent

    get_settings.cache_clear()
    app = create_app(store=fake_store)

    @app.get("/api/v1/_test/sessions/{session_id}/gated")  # test-only (doc 01 §11, A11)
    async def _gated(session_id: uuid.UUID = Depends(require_consent)) -> dict[str, str]:
        return {"ok": str(session_id)}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets", loop="asyncio"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(base + "/readyz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("test server did not become ready")
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
async def client(server: str):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(base_url=server, timeout=10) as c:
        yield c


async def register_and_login(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": "correct horse battery"})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def create_session(client: httpx.AsyncClient, headers: dict[str, str], **over: object) -> dict:
    body = {"mode": "upload", "product_type": "personal_loan", "language_hint": "en", **over}
    r = await client.post("/api/v1/sessions", json=body, headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 201, r.text
    return r.json()


async def current_texts(client: httpx.AsyncClient, locale: str = "en") -> dict[str, str]:
    r = await client.get("/api/v1/consent-texts", params={"locale": locale})
    assert r.status_code == 200
    return {t["kind"]: t["id"] for t in r.json()}


async def give_consent(client: httpx.AsyncClient, headers: dict[str, str], session_id: str,
                       agent_informed: str = "yes") -> httpx.Response:
    texts = await current_texts(client)
    body = {"consent_text_id": texts["processing"], "accepted": True,
            "attestation": {"attestation_text_id": texts["attestation"], "i_was_party": True,
                            "agent_informed": agent_informed}}
    return await client.post(f"/api/v1/sessions/{session_id}/consents", json=body,
                             headers={**headers, "Idempotency-Key": uuid.uuid4().hex})


async def scalar(engine, sql: str, **params: object):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()
