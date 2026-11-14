"""T3.5: shadow rules never visible (results, events, report) and the demo public-data engine (capped at UNCLEAR)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.llm.fakes import FakeEmbeddingProvider
from app.modules.contract.engine import ContractEngine
from app.modules.finance.engine import FinanceEngine
from app.modules.report import render
from app.modules.report.service import report_input
from app.modules.rules.engine import PublicDataEngine, RulesEngine
from app.modules.verify.service import verify_claim
from tests.conftest import scalar
from tests.integration.test_claims import pipeline
from tests.integration.test_documents import drain, upload
from tests.integration.test_finance_merge import Ag07Parser

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
AG07 = (Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements" / "AG07" / "agreement.pdf").read_bytes()


async def test_shadow_never_visible_and_public_data_capped(client, engine, fake_store, tone_files):  # type: ignore[no-untyped-def]
    h, sid, _ = await pipeline(client, engine, fake_store, tone_files)
    await upload(client, h, sid, [("ag07.pdf", AG07)])
    await drain(engine, fake_store, Ag07Parser())
    r = await client.put(f"/api/v1/sessions/{sid}/facts", headers=h, json={"borrower_type": "individual",
                                                                         "sanction_date": "2026-02-01"})
    assert r.status_code == 200
    engines = [ContractEngine(embedder=FakeEmbeddingProvider()), FinanceEngine(visible=lambda: False), RulesEngine(),
               PublicDataEngine()]
    claims = (await client.get(f"/api/v1/sessions/{sid}/claims", headers=h)).json()
    async with engine.begin() as conn:  # make insurance a charge outside the KFS table: a would-be rule CONFLICT
        await conn.execute(text("UPDATE clauses SET is_table_row = false WHERE text LIKE '%Insurance%' AND document_id IN "
                                "(SELECT id FROM documents WHERE session_id = :s)"), {"s": uuid.UUID(sid)})
        for c in claims:
            await verify_claim(conn, uuid.UUID(c["id"]), "manual_reverify", engines)
    s = uuid.UUID(sid)
    shadow = await scalar(engine, "SELECT count(*) FROM shadow_verdicts v JOIN claims c ON c.id = v.claim_id "
                                  "WHERE c.session_id = :s AND v.engine = 'rules'", s=s)
    assert shadow > 0  # the engine ran and stored would-be verdicts (incl. the AG07 penal-interest violation)
    assert await scalar(engine, "SELECT count(*) FROM shadow_verdicts v JOIN claims c ON c.id = v.claim_id WHERE "
                                "c.session_id = :s AND v.verdict->>'outcome' = 'violates'", s=s) > 0
    # Never visible: results, evidence, events, report.
    results = (await client.get(f"/api/v1/sessions/{sid}/results", headers=h, params={"include_hidden": True})).json()
    for x in results:
        assert x["primary_engine"] != "rules" and x["reason_code"] not in ("rule_not_enabled", "applicability_unknown")
        assert all(e["kind"] != "rule" for e in x["evidence"]) and x["finding_kind"] != "regulatory"
        assert all(v["engine"] != "rules" for v in x["engine_verdicts"])
    async with engine.connect() as conn:
        assert not (await conn.execute(text("SELECT 1 FROM events WHERE session_id = :s AND (payload::text ILIKE '%rule_not%' "
                                            "OR payload::text ILIKE '%RBI-%')"), {"s": s})).first()
        from datetime import UTC, datetime

        body = "\n".join(t for _, t in render.build(await report_input(conn, s, datetime.now(UTC))))
    assert "RBI" not in body and "rule" not in body.lower()
    # Public data: the fictional demo lender is found, capped at UNCLEAR(public_data_only); never CONFLICT.
    lender = next(x for x in results if x["headline"].endswith("lender"))
    assert (lender["status"], lender["reason_code"]) == ("UNCLEAR", "public_data_only")
    assert any(e["kind"] == "public" and "demo" in e["summary"].lower() for e in lender["evidence"])
    found = (await client.get("/api/v1/lenders/search", headers=h, params={"q": "Udyam Capital"})).json()
    assert found and found[0]["name"].endswith("(fictional)") and found[0]["note"].startswith("Demo data")
    assert (await client.get("/api/v1/lenders/search", headers=h, params={"q": "State Bank"})).json() == []
