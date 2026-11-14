"""Report job `report.render:{report_id}` and API — doc 11 §2 (POST/GET reports), §6 (`exports/{session}/{report}.pdf`)."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import get_engine
from app.core.deps import client_ip, current_user, idempotent_create
from app.core.errors import AppError
from app.core.storage import ObjectStore
from app.modules.audit import service as audit
from app.modules.consent.gate import require_consent
from app.modules.consent.service import consent_status
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.report import render
from app.modules.sessions.service import get_owned_session
from app.modules.verify.service import load_results
from app.schemas.verification import ReasonCode
from app.worker import queue
from app.worker.runner import Handler, JobContext

router = APIRouter(prefix="/api/v1", tags=["reports"])


async def report_input(conn: AsyncConnection, session_id: uuid.UUID, now: datetime) -> render.ReportInput:
    s = (await conn.execute(text("SELECT lender_name_raw, product_type, language_hint FROM sessions WHERE id = :s"),
                            {"s": session_id})).first()
    cs = await consent_status(conn, session_id)
    results = await load_results(conn, "r.is_current AND r.claim_id IN (SELECT id FROM claims WHERE session_id = :s "
                                       "AND state = 'active')", {"s": session_id})
    return render.ReportInput(session_id=str(session_id), lender=s.lender_name_raw if s else None,
                              product=s.product_type if s else None, language_hint=s.language_hint if s else None,
                              attestation_note=cs.attestation_note, consent_text_version=cs.consent_text_version,
                              generated_at=now, results=[r for r in results if r.reason_code != ReasonCode.NOT_AGENT_CLAIM])


def make_report_handler(store: ObjectStore) -> Handler:
    async def handle(ctx: JobContext) -> None:
        rid = uuid.UUID(ctx.job.payload["report_id"])
        row = (await ctx.conn.execute(text("SELECT session_id, status FROM reports WHERE id = :id"), {"id": rid})).first()
        if row is None or row.status == "ready":
            return
        inp = await report_input(ctx.conn, row.session_id, datetime.now(UTC))
        pdf = render.to_pdf(render.build(inp))
        await ctx.checkpoint()
        key = f"{row.session_id}/{rid}.pdf"
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "r.pdf").write_bytes(pdf)
            await store.put_file("exports", key, str(Path(t) / "r.pdf"), "application/pdf")
        versions = {str(r.claim_id): r.version for r in inp.results}
        await ctx.conn.execute(text("UPDATE reports SET status = 'ready', object_key = :k, result_versions = CAST(:v AS jsonb) "
                                    "WHERE id = :id"), {"k": key, "v": json.dumps(versions), "id": rid})
        await audit.record(ctx.conn, "report.generate", actor_id=None, target_type="report", target_id=rid,
                           meta={"results": len(inp.results)})
        await publish(ctx.conn, row.session_id, EventType.REPORT_STATUS, {"report_id": str(rid), "status": "ready"})

    return handle


class ReportOut(BaseModel):
    report_id: uuid.UUID
    status: str
    url: str | None = None
    created_at: datetime | None = None


@router.post("/sessions/{session_id}/reports", status_code=202, response_model=ReportOut)
async def create_report(request: Request, session_id: uuid.UUID = Depends(require_consent),
                        user_id: uuid.UUID = Depends(current_user)):  # type: ignore[no-untyped-def]
    async def handler() -> ReportOut:
        async with get_engine().begin() as conn:
            await get_owned_session(conn, session_id, user_id)
            rid = uuid.uuid4()
            await conn.execute(text("INSERT INTO reports (id, session_id, status) VALUES (:id, :s, 'queued')"),
                               {"id": rid, "s": session_id})
            await queue.enqueue(conn, "report.render", {"report_id": str(rid)}, session_id=session_id,
                                dedupe_key=f"report:{rid}")
            await publish(conn, session_id, EventType.REPORT_STATUS, {"report_id": str(rid), "status": "queued"})
            return ReportOut(report_id=rid, status="queued")

    return await idempotent_create(request, user_id, 202, handler)


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(report_id: uuid.UUID, request: Request, user_id: uuid.UUID = Depends(current_user)) -> ReportOut:
    async with get_engine().begin() as conn:
        row = (await conn.execute(text(
            "SELECT r.* FROM reports r JOIN sessions s ON s.id = r.session_id WHERE r.id = :id AND s.owner_id = :u "
            "AND s.deleted_at IS NULL"), {"id": report_id, "u": user_id})).mappings().first()
        if row is None:
            raise AppError("not_found", "Report not found")
        url = None
        if row["status"] == "ready":
            url = await request.app.state.store.get_url("exports", row["object_key"], 300)
            await audit.record(conn, "report.download", actor_id=user_id, target_type="report", target_id=report_id,
                               ip=client_ip(request))
    return ReportOut(report_id=report_id, status=row["status"], url=url, created_at=row["created_at"])
