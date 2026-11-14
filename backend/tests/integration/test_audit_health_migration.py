import os
import subprocess
import sys

import psycopg
import pytest
from sqlalchemy import text

from tests.conftest import BACKEND, BASE_URL

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_audit_log_is_append_only(engine):
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO audit_log (action) VALUES ('session.create')"))
    for stmt in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(Exception, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(stmt))


async def test_readyz_reports_db_storage_listener(client, fake_store):
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"db": "ok", "storage": "ok", "listener": "ok"}
    fake_store.ok = False
    try:
        r = await client.get("/readyz")
        assert r.status_code == 503 and r.json()["storage"].startswith("error")
    finally:
        fake_store.ok = True
    assert (await client.get("/healthz")).json() == {"status": "ok"}


async def test_unknown_route_is_problem_json(client):
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_migration_upgrade_downgrade_upgrade():
    scratch = "saidsigned_migration_test"
    admin = BASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {scratch}")
    url = BASE_URL.rsplit("/", 1)[0] + "/" + scratch
    env = {**os.environ, "DATABASE_URL": url}
    for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True)
    with psycopg.connect(admin.rsplit("/", 1)[0] + "/" + scratch) as c:
        tables = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert tables == {"alembic_version", "users", "refresh_tokens", "ws_tickets", "sessions", "consent_texts",
                      "consents", "idempotency_keys", "jobs", "events", "audit_log", "deletion_receipts",
                      "audio_streams", "transcript_revisions", "transcript_segments", "transcript_words",
                      "speaker_assignments", "claims", "claim_values",
                      "documents", "document_pages", "clauses", "doc_terms", "chunks", "calculations",
                      "verification_runs", "verification_results", "evidence_links", "shadow_verdicts"}
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
