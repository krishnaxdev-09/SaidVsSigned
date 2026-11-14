"""Verification orchestrator v1 — doc 08 §5–§9 (T2.5).

verify_claim: load the claim → run every applicable engine (sequentially on the job's connection; per-engine timeout;
an exception becomes NOT_VERIFIED(engine_error)) → gates → shadow verdicts stored apart → merge → integrity
validator → persist a new result version (old one `is_current = false`; nothing overwritten) with evidence links →
publish `verification.updated`.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import structlog
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings, load_features
from app.llm.base import EmbeddingProvider, LLMProvider
from app.modules.claims.service import load_claims
from app.modules.realtime.bus import publish
from app.modules.realtime.events import EventType
from app.modules.verify import gates, integrity
from app.modules.verify.merge import Merged, Outcome, merge
from app.schemas.claims import Claim
from app.schemas.verification import (
    DocumentEvidence,
    Engine,
    EngineVerdict,
    Evidence,
    ReasonCode,
    SpokenEvidence,
    Status,
    StatusChange,
    VerificationResult,
)
from app.schemas.wording import FIELD_LABELS, REASON_TEXT, headline

log = structlog.get_logger()
TIMEOUTS: dict[str, float] = {"contract": 30.0, "finance": 2.0, "rules": 2.0, "public_data": 3.0}
Trigger = Literal["initial", "document_ready", "document_replaced", "speaker_confirmed", "quote_confirmed",
                  "transcript_revised", "claim_corrected", "manual_reverify", "thresholds_changed", "rule_enabled"]
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass
class EngineContext:
    conn: AsyncConnection
    session_id: uuid.UUID
    owner_id: uuid.UUID
    thresholds: gates.Thresholds
    embedder: EmbeddingProvider | None = None
    llm: LLMProvider | None = None
    hindi: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class VerificationEngine(Protocol):
    @property
    def name(self) -> Engine: ...

    @property
    def shadow(self) -> bool: ...

    def applies(self, claim: Claim) -> bool: ...

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome: ...


def load_thresholds() -> gates.Thresholds:
    t = yaml.safe_load(get_settings().THRESHOLDS_FILE.read_text())
    return gates.Thresholds(version=str(t["version"]), T_asr_num=float(t["T_asr_num"]), T_asr_avg=float(t["T_asr_avg"]),
                            T_ocr=float(t["T_ocr"]), T_ocr_num=float(t["T_ocr_num"]),
                            T_ret=None if t.get("T_ret") is None else float(t["T_ret"]),
                            T_nli_contra=None if t.get("T_nli_contra") is None else float(t["T_nli_contra"]),
                            T_nli_entail=None if t.get("T_nli_entail") is None else float(t["T_nli_entail"]))


def visibility() -> tuple[bool, bool]:
    f = load_features(get_settings().FEATURES_FILE)
    fin = f.get("finance", {})
    finance = bool(fin.get("mvp_formulas_user_visible") and (fin.get("hc5_approval") or {}).get("approved_by"))
    return finance, False  # rules stay shadow until per-rule HC-6 approval (no rule is enabled in this build)


def is_hindi(language_hint: str | None, segment_language: str | None, quote: str) -> bool:
    """HC-3 language policy (doc 03 §8): STT reports `hin` for both Hindi and Hinglish, so use the session hint, and
    without a hint treat a mostly-Devanagari quote in a Hindi-detected segment as Hindi (not yet supported)."""
    if language_hint == "hi":
        return True
    if language_hint in ("en", "hinglish"):
        return False
    if not (segment_language or "").lower().startswith("hi"):
        return False
    return len(_DEVANAGARI.findall(quote)) > len(_LATIN.findall(quote))


def spoken_evidence(claim: Claim) -> SpokenEvidence:
    return SpokenEvidence(claim_id=claim.id, quote=claim.quote, quote_anchors=claim.quote_anchors,
                          start_ms=claim.start_ms, end_ms=claim.end_ms, speaker_role=claim.speaker_role,
                          speaker_source=claim.speaker_source, role_assignment=claim.role_assignment,
                          asr_avg_conf=claim.asr_avg_conf, asr_min_numeric_conf=claim.asr_min_numeric_conf,
                          quote_confirmed_by_user=claim.quote_confirmed_by_user)


def error_verdict(engine: Engine, error_class: Literal["transient", "schema", "input", "internal"]) -> EngineVerdict:
    return EngineVerdict(engine=engine, status=Status.NOT_VERIFIED, reason_code=ReasonCode.ENGINE_ERROR,
                         finding_kind=None, reason=REASON_TEXT[ReasonCode.ENGINE_ERROR], evidence=[],
                         error_class=error_class)


async def run_engines(engines: list[VerificationEngine], claim: Claim, ctx: EngineContext) -> list[Outcome]:
    out: list[Outcome] = []
    for e in engines:
        if not e.applies(claim):
            continue
        try:
            o = await asyncio.wait_for(e.verify(claim, ctx), TIMEOUTS.get(e.name, 30.0))
        except TimeoutError:
            log.warning("engine_timeout", engine=e.name, claim_id=str(claim.id))
            o = Outcome(error_verdict(e.name, "transient"), shadow=e.shadow)
        except Exception:  # noqa: BLE001 — any engine failure is NOT_VERIFIED(engine_error), never MATCH
            log.exception("engine_failed", engine=e.name, claim_id=str(claim.id))
            o = Outcome(error_verdict(e.name, "internal"), shadow=e.shadow)
        o.shadow = o.shadow or e.shadow
        out.append(o)
    return out


async def _session(conn: AsyncConnection, session_id: uuid.UUID) -> Any:
    return (await conn.execute(text("SELECT owner_id, language_hint FROM sessions WHERE id = :s"),
                               {"s": session_id})).first()


async def verify_claim(conn: AsyncConnection, claim_id: uuid.UUID, trigger: Trigger, engines: list[VerificationEngine],
                       *, embedder: EmbeddingProvider | None = None, llm: LLMProvider | None = None
                       ) -> VerificationResult | None:
    sid = (await conn.execute(text("SELECT session_id FROM claims WHERE id = :id"), {"id": claim_id})).scalar()
    if sid is None:
        return None
    claims = await load_claims(conn, sid, claim_id=claim_id)
    if not claims or claims[0].state != "active":
        return None  # dismissed or superseded claims keep their last result
    claim = claims[0]
    sess = await _session(conn, sid)
    seg_lang = (await conn.execute(text("SELECT language FROM transcript_segments WHERE id = :id"),
                                   {"id": claim.quote_anchors[0].segment_id})).scalar()
    th = load_thresholds()
    ctx = EngineContext(conn=conn, session_id=sid, owner_id=sess.owner_id, thresholds=th, embedder=embedder, llm=llm,
                        hindi=is_hindi(sess.language_hint, seg_lang, claim.quote))
    outcomes = await run_engines(engines, claim, ctx)
    if not any(not o.shadow for o in outcomes):
        outcomes.append(Outcome(EngineVerdict(
            engine="contract", status=Status.NOT_VERIFIED, reason_code=ReasonCode.ENGINE_NOT_APPLICABLE,
            finding_kind=None, reason=REASON_TEXT[ReasonCode.ENGINE_NOT_APPLICABLE], evidence=[])))
    for o in outcomes:
        o.verdict = gates.apply_gates(o.verdict, claim, th, hindi=ctx.hindi)
    for o in outcomes:
        if o.shadow:
            await conn.execute(text("INSERT INTO shadow_verdicts (id, claim_id, engine, rule_id, verdict) "
                                    "VALUES (:id, :c, :e, NULL, CAST(:v AS jsonb))"),
                               {"id": uuid.uuid4(), "c": claim.id, "e": o.verdict.engine,
                                "v": o.verdict.model_dump_json()})
    spoken = spoken_evidence(claim)
    doc_ids = [str(e.clause_id) for o in outcomes for e in o.verdict.evidence if isinstance(e, DocumentEvidence)]
    sections = {str(r.id): r.section_path for r in (await conn.execute(text(
        "SELECT id, section_path FROM clauses WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": doc_ids})).all()}
    finance_visible, rules_visible = visibility()
    m = merge(claim, spoken, outcomes, finance_user_visible=finance_visible, rules_user_visible=rules_visible,
              sections=sections)
    if m.status in (Status.CONFLICT, Status.MATCH) or any(not isinstance(e, SpokenEvidence) for e in m.evidence):
        if await integrity.check(conn, m.evidence):
            label = FIELD_LABELS.get(claim.claim_type.value, claim.claim_type.value)
            m = Merged(Status.NOT_VERIFIED, ReasonCode.ENGINE_ERROR, None, headline(Status.NOT_VERIFIED, label),
                       REASON_TEXT[ReasonCode.ENGINE_ERROR], "info", m.primary_engine, [spoken], None, m.verdicts)
    return await persist(conn, claim, m, trigger, th.version)


async def persist(conn: AsyncConnection, claim: Claim, m: Merged, trigger: Trigger, thresholds_version: str
                  ) -> VerificationResult:
    await conn.execute(text("SELECT 1 FROM claims WHERE id = :id FOR UPDATE"), {"id": claim.id})
    prev = (await conn.execute(text(
        "SELECT version, status FROM verification_results WHERE claim_id = :c AND is_current"), {"c": claim.id})).first()
    change = StatusChange(trigger="initial" if prev is None else trigger,
                          previous_status=Status(prev.status) if prev else None,
                          previous_version=prev.version if prev else None)
    run_id, rid, now = uuid.uuid4(), uuid.uuid4(), datetime.now(UTC)
    result = VerificationResult(
        id=rid, claim_id=claim.id, run_id=run_id, version=(prev.version + 1) if prev else 1, is_current=True,
        status=m.status, reason_code=m.reason_code, finding_kind=m.finding_kind, headline=m.headline, reason=m.reason,
        severity=m.severity, primary_engine=m.primary_engine, engine_verdicts=m.verdicts, evidence=m.evidence,
        suggested_question=m.suggested_question, changed_because=change, thresholds_version=thresholds_version,
        created_at=now)
    await conn.execute(text("INSERT INTO verification_runs (id, session_id, trigger, thresholds_version, "
                            "features_version) VALUES (:id, :s, :t, :tv, NULL)"),
                       {"id": run_id, "s": claim.session_id, "t": trigger, "tv": thresholds_version})
    await conn.execute(text("UPDATE verification_results SET is_current = false WHERE claim_id = :c AND is_current"),
                       {"c": claim.id})
    await conn.execute(text(
        "INSERT INTO verification_results (id, claim_id, run_id, version, is_current, status, reason_code, "
        "finding_kind, headline, reason, severity, primary_engine, engine_verdicts, suggested_question, "
        "changed_because, thresholds_version, created_at) VALUES (:id, :c, :r, :v, true, :st, :rc, :fk, :h, :re, "
        ":sev, :pe, CAST(:ev AS jsonb), :q, CAST(:cb AS jsonb), :tv, :at)"),
        {"id": rid, "c": claim.id, "r": run_id, "v": result.version, "st": m.status.value,
         "rc": m.reason_code.value if m.reason_code else None, "fk": m.finding_kind.value if m.finding_kind else None,
         "h": m.headline, "re": m.reason, "sev": m.severity, "pe": m.primary_engine,
         "ev": json.dumps([json.loads(v.model_dump_json()) for v in m.verdicts]), "q": m.suggested_question,
         "cb": change.model_dump_json(), "tv": thresholds_version, "at": now})
    for e in m.evidence:
        p = json.loads(e.model_dump_json())
        await conn.execute(text(
            "INSERT INTO evidence_links (id, result_id, kind, quote, quote_anchors, start_ms, end_ms, speaker_role, "
            "role_assignment, document_id, document_version, page, clause_id, clause_text, bboxes, ocr_conf, "
            "calculation_id, rule_id, rule_version, source_url, payload) VALUES (:id, :r, :k, :q, CAST(:qa AS jsonb), "
            ":sm, :em, :sr, :ra, :d, :dv, :p, :cl, :ct, CAST(:bb AS jsonb), :oc, :ca, :ru, :rv, :su, "
            "CAST(:pl AS jsonb))"),
            {"id": uuid.uuid4(), "r": rid, "k": e.kind, "q": p.get("quote"),
             "qa": json.dumps(p.get("quote_anchors")), "sm": p.get("start_ms"), "em": p.get("end_ms"),
             "sr": p.get("speaker_role"), "ra": p.get("role_assignment"), "d": p.get("document_id"),
             "dv": p.get("document_version"), "p": p.get("page"), "cl": p.get("clause_id"),
             "ct": p.get("clause_text"), "bb": json.dumps(p.get("bboxes")), "oc": p.get("ocr_conf"),
             "ca": p.get("calculation_id"), "ru": p.get("rule_id"), "rv": p.get("rule_version"),
             "su": p.get("source_url"), "pl": json.dumps(p)})
    await publish(conn, claim.session_id, EventType.VERIFICATION_UPDATED, json.loads(result.model_dump_json()))
    return result


async def load_results(conn: AsyncConnection, where: str, params: dict[str, Any]) -> list[VerificationResult]:
    rows = (await conn.execute(text(f"SELECT r.* FROM verification_results r WHERE {where} ORDER BY r.created_at, r.id"),
                               params)).mappings().all()
    if not rows:
        return []
    ev = (await conn.execute(text("SELECT result_id, payload FROM evidence_links WHERE result_id = ANY(:ids) ORDER BY id"),
                             {"ids": [r["id"] for r in rows]})).all()
    by: dict[uuid.UUID, list[Any]] = {}
    for e in ev:
        by.setdefault(e.result_id, []).append(e.payload)
    kinds = {"spoken": 0, "document": 1, "calculation": 2, "rule": 3, "public": 4}
    out = []
    for r in rows:
        evidence = sorted(by.get(r["id"], []), key=lambda p: kinds.get(p["kind"], 9))
        out.append(VerificationResult.model_validate({
            **{k: r[k] for k in ("id", "claim_id", "run_id", "version", "is_current", "status", "reason_code",
                                 "finding_kind", "headline", "reason", "severity", "primary_engine",
                                 "suggested_question", "thresholds_version", "created_at")},
            "engine_verdicts": r["engine_verdicts"], "evidence": evidence, "changed_because": r["changed_because"]}))
    return out


__all__ = ["EngineContext", "Evidence", "Outcome", "VerificationEngine", "load_results", "verify_claim"]
