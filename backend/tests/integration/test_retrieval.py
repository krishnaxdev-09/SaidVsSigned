"""T2.4: chunks + hybrid retrieval (doc 09 §3–§4): scope to owner/session/current version/ready, synonyms, RRF, T_ret."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.llm.fakes import FakeEmbeddingProvider
from app.modules.rag import retrieve
from tests.conftest import scalar
from tests.integration.test_documents import KfsParser, drain, pdf_bytes, upload
from tests.integration.test_transcripts import ready_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
EMB = FakeEmbeddingProvider()


async def _owner(engine, sid: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return await scalar(engine, "SELECT owner_id FROM sessions WHERE id = :s", s=uuid.UUID(sid))


async def _ask(engine, sid, uid, q, **kw):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return await retrieve.retrieve(conn, EMB, session_id=uuid.UUID(str(sid)), user_id=uid, query=q, **kw)


async def test_index_and_hybrid_retrieval_scope(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    did = (await upload(client, h, sid, [("kfs.pdf", pdf_bytes(2))])).json()["document_id"]
    await drain(engine, fake_store, KfsParser())
    assert (await client.get(f"/api/v1/documents/{did}", headers=h)).json()["status"] == "ready"
    assert await scalar(engine, "SELECT count(*) FROM chunks WHERE document_id = :d AND embedding IS NOT NULL "
                                "AND embedding_model = 'fake-embedding'", d=uuid.UUID(did)) == 5
    uid = await _owner(engine, sid)

    top = await _ask(engine, sid, uid, "Is there a foreclosure charge?", use_threshold=False)
    assert "Prepayment" in top[0].text and top[0].bm25_rank == 1  # foreclosure → prepayment synonym
    top = await _ask(engine, sid, uid, "processing fee kitni hai?", use_threshold=False)
    assert "Processing fee" in top[0].text and len(top) <= 5
    assert top[0].rrf > 2 / 61  # money query: table row / doc_terms clause boosted by +0.02

    # Scope: another user, another session, nothing leaks.
    h2, sid2 = await ready_session(client)
    assert await _ask(engine, sid2, await _owner(engine, sid2), "processing fee", use_threshold=False) == []
    assert await _ask(engine, sid, await _owner(engine, sid2), "processing fee", use_threshold=False) == []

    # A document that is not ready, or an old version, is never retrieved.
    async with engine.begin() as c:
        await c.execute(text("UPDATE documents SET status = 'indexing' WHERE id = :d"), {"d": uuid.UUID(did)})
    assert await _ask(engine, sid, uid, "processing fee", use_threshold=False) == []
    async with engine.begin() as c:
        await c.execute(text("UPDATE documents SET status = 'ready', version = 2 WHERE id = :d"), {"d": uuid.UUID(did)})
    assert await _ask(engine, sid, uid, "processing fee", use_threshold=False) == []


async def test_t_ret_returns_empty_below_threshold(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    await upload(client, h, sid, [("kfs.pdf", pdf_bytes(2))])
    await drain(engine, fake_store, KfsParser())
    uid = await _owner(engine, sid)
    assert await _ask(engine, sid, uid, "zebra galaxy volcano", threshold=0.5) == []
    assert await _ask(engine, sid, uid, "Prepayment attracts a charge", threshold=0.5) != []


def test_expand_and_chunking() -> None:
    assert {"prepayment", "foreclosure", "jaldi", "band"} <= set(retrieve.expand("Foreclosure charge?"))
    assert {"emi", "kist"} <= set(retrieve.expand("meri kist kitni hai"))
    assert retrieve.is_money_query("processing fee kitni hai") and not retrieve.is_money_query("governing law?")
    long = " ".join(f"Sentence {i} about the loan terms and conditions." for i in range(100))
    parts = retrieve.chunk_texts(long)
    assert len(parts) > 1 and all(len(p) <= retrieve.MAX_CHUNK_CHARS for p in parts) and " ".join(parts) == long
    assert retrieve.chunk_texts("short") == ["short"]
