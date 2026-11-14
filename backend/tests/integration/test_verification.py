"""T2.5 orchestrator: claim → engines → gates → merge → integrity → versioned results, triggers and results API."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.llm.fakes import FakeEmbeddingProvider
from app.modules.documents.ingest import make_doc_ingest_handler
from app.modules.verify.engines import FinanceEngine
from app.modules.verify.job import make_verify_handler
from app.modules.verify.merge import Outcome
from app.schemas.verification import DocumentEvidence, EngineVerdict, FindingKind, NormBBox, ReasonCode, Status
from app.worker.runner import Registry, Worker
from tests.conftest import register_and_login, scalar
from tests.integration.test_claims import pipeline
from tests.integration.test_documents import KfsParser, pdf_bytes
from tests.integration.test_documents import upload as upload_doc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class FakeContract:
    """Stands in for the T2.6 contract engine: no ready document → no_document; else MATCH on the first clause."""

    name = "contract"
    shadow = False

    def __init__(self, tamper: bool = False) -> None:
        self.tamper = tamper

    def applies(self, claim) -> bool:  # type: ignore[no-untyped-def]
        return claim.claim_type.value != "lender_identity"

    async def verify(self, claim, ctx):  # type: ignore[no-untyped-def]
        row = (await ctx.conn.execute(text(
            "SELECT c.* FROM clauses c JOIN documents d ON d.id = c.document_id AND d.version = c.version "
            "WHERE d.session_id = :s AND d.status = 'ready' ORDER BY c.ord LIMIT 1"), {"s": ctx.session_id})).mappings().first()
        if row is None:
            return Outcome(EngineVerdict(engine="contract", status=Status.NOT_VERIFIED, reason_code=ReasonCode.NO_DOCUMENT,
                                         finding_kind=None, reason="r", evidence=[]))
        b = row["bboxes"][0]["bbox"]
        ev = DocumentEvidence(document_id=row["document_id"], document_version=row["version"], page=row["page_start"],
                              printed_page=None, clause_id=row["id"],
                              clause_text=row["text"] + (" (edited)" if self.tamper else ""),
                              bboxes=[NormBBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3])], ocr_conf=row["ocr_conf"],
                              ocr_min_numeric_conf=row["ocr_min_numeric_conf"], text_source=row["text_source"])
        return Outcome(EngineVerdict(engine="contract", status=Status.MATCH, reason_code=None,
                                     finding_kind=FindingKind.CONTRACTUAL, reason="r", evidence=[ev]))


async def run_jobs(engine, store, contract=None) -> None:  # type: ignore[no-untyped-def]
    reg = Registry()
    reg.register("doc.ingest", make_doc_ingest_handler(store, KfsParser(), None, FakeEmbeddingProvider()))
    reg.register("verify", make_verify_handler([contract or FakeContract(), FinanceEngine()]))
    w = Worker(engine, reg, lease_seconds=60)
    for _ in range(3):  # verify jobs are debounced 2 s: make them due, then drain
        async with engine.begin() as c:
            await c.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'queued'"))
        while await w.run_once():
            pass


async def results(client, h, sid, **params):  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/v1/sessions/{sid}/results", headers=h, params=params)
    assert r.status_code == 200, r.text
    return {x["claim_id"]: x for x in r.json()}


async def test_versioned_results_and_document_trigger(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    await run_jobs(engine, fake_store)
    claims = {c["id"]: c for c in (await client.get(f"/api/v1/sessions/{sid}/claims", headers=h)).json()}
    v1 = await results(client, h, sid, include_hidden=True)
    assert set(v1) == set(claims)  # every active claim was verified after extraction
    fee = next(c for c in claims.values() if c["claim_type"] == "processing_fee" and c["verifiable"])
    r1 = v1[fee["id"]]
    assert (r1["status"], r1["reason_code"], r1["version"]) == ("NOT_VERIFIED", "no_document", 1)
    assert r1["changed_because"]["trigger"] == "initial" and r1["evidence"][0]["kind"] == "spoken"
    hidden = await results(client, h, sid)
    assert all(x["reason_code"] != "not_agent_claim" for x in hidden.values()) and len(hidden) < len(v1)

    await upload_doc(client, h, sid, [("kfs.pdf", pdf_bytes(2))])
    await run_jobs(engine, fake_store)
    r2 = (await results(client, h, sid))[fee["id"]]
    assert (r2["status"], r2["version"], r2["severity"]) == ("MATCH", 2, "info")  # MATCH allowed with an auto role
    assert r2["changed_because"] == {"trigger": "document_ready", "previous_status": "NOT_VERIFIED", "previous_version": 1}
    assert [e["kind"] for e in r2["evidence"]] == ["spoken", "document"] and r2["evidence"][1]["bboxes"]
    hist = (await client.get(f"/api/v1/results/{r2['id']}/history", headers=h)).json()
    assert [(x["version"], x["is_current"]) for x in hist] == [(1, False), (2, True)]
    assert await scalar(engine, "SELECT count(*) FROM events WHERE session_id = :s AND type = 'verification.updated'",
                        s=uuid.UUID(sid)) >= len(v1) + 1

    # Owner only.
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/sessions/{sid}/results", headers=other)).status_code == 404
    assert (await client.get(f"/api/v1/results/{r2['id']}/history", headers=other)).status_code == 404

    # Integrity validator: evidence that doesn't match the stored clause never becomes MATCH/CONFLICT.
    assert (await client.post(f"/api/v1/sessions/{sid}/reverify", headers=h)).json()["queued"] == len(claims)
    await run_jobs(engine, fake_store, FakeContract(tamper=True))
    r3 = (await results(client, h, sid))[fee["id"]]
    assert (r3["status"], r3["reason_code"], r3["version"]) == ("NOT_VERIFIED", "engine_error", 3)
    assert r3["changed_because"]["trigger"] == "manual_reverify"


async def test_quote_confirmation(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    cs = (await client.get(f"/api/v1/sessions/{sid}/claims", headers=h)).json()
    fee = next(c for c in cs if c["claim_type"] == "processing_fee" and c["verifiable"])
    r = await client.post(f"/api/v1/claims/{fee['id']}/confirm-quote", headers=h, json={"confirmed": True})
    assert r.status_code == 200 and r.json()["quote_confirmed_by_user"] is True
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'claim.confirm_quote' AND target_id = :c",
                        c=uuid.UUID(fee["id"])) == 1
    assert await scalar(engine, "SELECT payload->>'trigger' FROM jobs WHERE dedupe_key = :k AND status = 'queued'",
                        k=f"verify:{fee['id']}") in ("quote_confirmed", "transcript_revised")
    _, other = await register_and_login(client)
    assert (await client.post(f"/api/v1/claims/{fee['id']}/confirm-quote", headers=other,
                              json={"confirmed": True})).status_code == 404
    # Hindi session: not offered for numeric claims (HC-3).
    async with engine.begin() as c:
        await c.execute(text("UPDATE sessions SET language_hint = 'hi' WHERE id = :s"), {"s": uuid.UUID(sid)})
    r = await client.post(f"/api/v1/claims/{fee['id']}/confirm-quote", headers=h, json={"confirmed": True})
    assert r.status_code == 409, r.text
