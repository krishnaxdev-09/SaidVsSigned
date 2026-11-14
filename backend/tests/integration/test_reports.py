"""T3.3 report API + job: consent required, idempotent create, render to exports, presigned download, owner only."""

from __future__ import annotations

import uuid

import pytest

from app.modules.report.service import make_report_handler
from app.worker.runner import Registry, Worker
from tests.conftest import register_and_login, scalar
from tests.integration.test_transcripts import ready_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_report_flow(client, engine, fake_store):  # type: ignore[no-untyped-def]
    h, sid = await ready_session(client)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    r = await client.post(f"/api/v1/sessions/{sid}/reports", headers={**h, **key})
    assert r.status_code == 202, r.text
    rid = r.json()["report_id"]
    assert (await client.post(f"/api/v1/sessions/{sid}/reports", headers={**h, **key})).json()["report_id"] == rid
    assert (await client.get(f"/api/v1/reports/{rid}", headers=h)).json() == {
        "report_id": rid, "status": "queued", "url": None, "created_at": (await client.get(f"/api/v1/reports/{rid}",
                                                                                        headers=h)).json()["created_at"]}
    reg = Registry()
    reg.register("report.render", make_report_handler(fake_store))
    w = Worker(engine, reg, lease_seconds=60)
    while await w.run_once():
        pass
    out = (await client.get(f"/api/v1/reports/{rid}", headers=h)).json()
    assert out["status"] == "ready" and out["url"].endswith(f"{sid}/{rid}.pdf")
    pdf = fake_store.objects[("exports", f"{sid}/{rid}.pdf")]
    assert pdf.startswith(b"%PDF")
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'report.generate' AND target_id = :r",
                        r=uuid.UUID(rid)) == 1
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE action = 'report.download' AND target_id = :r",
                        r=uuid.UUID(rid)) == 1
    _, other = await register_and_login(client)
    assert (await client.get(f"/api/v1/reports/{rid}", headers=other)).status_code == 404
    assert (await client.post(f"/api/v1/sessions/{sid}/reports", headers={**other, "Idempotency-Key": "x1"})).status_code \
        in (403, 404)
    await client.delete(f"/api/v1/sessions/{sid}/consents/customer", headers=h)
    r = await client.post(f"/api/v1/sessions/{sid}/reports", headers={**h, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 403 and r.json()["code"] == "consent_required"
