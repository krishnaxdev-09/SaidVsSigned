"""Shared eval helper: ingest synthetic agreements with the real pipeline into a scratch database (dropped after)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.modules.documents import ingest, parse, segment, terms
from app.modules.rag import retrieve

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend"


def create(name: str) -> tuple[AsyncEngine, str]:
    base = os.environ["DATABASE_URL"]
    admin = base.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {name}")
    url = base.rsplit("/", 1)[0] + "/" + name
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                   env={**os.environ, "DATABASE_URL": url}, check=True, capture_output=True)
    return create_async_engine(url), admin


def drop(name: str, admin: str) -> None:
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


async def ingest_agreement(engine: AsyncEngine, parser, llm, emb, uid: uuid.UUID, ag: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    """Returns the session id holding the ready document."""
    pdf = ROOT / "datasets" / "agreements" / ag / "agreement.pdf"
    d = parser.parse(pdf, ocr=False, force_full_page_ocr=False)
    parse.confidence_pass(d, set())
    clauses = segment.segment(d.items)
    lex = terms.lexicon_terms(clauses)
    llm_terms, _ = await terms.llm_terms(llm, clauses, {t.clause_ord for t in lex})
    chunks = [(c, piece) for c in clauses for piece in retrieve.chunk_texts(c.text)]
    vecs = await emb.embed([((c.section_path + "\n") if c.section_path else "") + p for c, p in chunks], kind="passage")
    sid, did = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (id, email, password_hash) VALUES (:u, :e, 'x') ON CONFLICT DO NOTHING"),
                           {"u": uid, "e": f"{uid}@example.com"})
        await conn.execute(text("INSERT INTO sessions (id, owner_id, mode, state, retention_until) VALUES "
                                "(:s, :u, 'upload', 'ready', now() + interval '1 day')"), {"s": sid, "u": uid})
        await conn.execute(text("INSERT INTO documents (id, session_id, kind, source, version, content_sha256, status) "
                                "VALUES (:d, :s, 'loan_agreement', 'pdf', 1, :h, 'ready')"), {"d": did, "s": sid, "h": ag})
        ids = await ingest._write_clauses(SimpleNamespace(conn=conn), did, 1, clauses, lex + llm_terms)  # type: ignore[arg-type]
        for (c, piece), vec in zip(chunks, vecs, strict=True):
            await conn.execute(text(
                "INSERT INTO chunks (id, document_id, version, clause_id, page, section_path, text, embedding, "
                "embedding_model) VALUES (:id, :d, 1, :c, :p, :sp, :t, CAST(:e AS vector), :m)"),
                {"id": uuid.uuid4(), "d": did, "c": ids[c.ord], "p": c.page_start, "sp": c.section_path, "t": piece,
                 "e": "[" + ",".join(f"{x:.6f}" for x in vec) + "]", "m": emb.name})
    return sid
