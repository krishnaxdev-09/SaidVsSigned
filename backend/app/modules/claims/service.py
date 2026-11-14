"""Claim persistence and read model (doc 04 §4, §8; doc 11 claims / claim_values). Speaker fields are resolved
from speaker_assignments at read time (doc 03 §5), so confirming roles later needs no re-extraction."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.ids import uuid7
from app.modules.claims.extract import PROMPT_VERSION, BuiltClaim, readiness_of
from app.modules.speakers.service import resolve
from app.schemas.claims import VERIFIABLE_KINDS, Claim, Condition, ExtractedValue, UtteranceKind
from app.schemas.verification import QuoteAnchor

CLAIM_COLUMNS = ("id, session_id, transcript_revision, claim_type, utterance_kind, speaker_label, normalized_statement, "
                 "quote, quote_anchors, also_said_at, start_ms, end_ms, conditions, vagueness, extraction_confidence, "
                 "asr_avg_conf, asr_min_numeric_conf, quote_confirmed_by_user, readiness, missing_inputs, state, "
                 "supersedes_claim_id, prompt_version")
VALUE_FIELDS = ("field", "raw_text", "quantity", "value", "value_low", "value_high", "comparator", "period",
                "rate_basis", "rate_type", "of_what", "includes_gst", "normaliser_version")


def _anchors_json(anchors: list[QuoteAnchor]) -> str:
    return json.dumps([a.model_dump(mode="json") for a in anchors])


async def replace_revision(conn: AsyncConnection, session_id: uuid.UUID, rev: int, claims: list[BuiltClaim]) -> None:
    """Rewrite this revision's claims in one transaction (doc 04 §8); older revisions' claims become superseded."""
    await conn.execute(text(
        "UPDATE claims SET state = 'superseded', updated_at = now() WHERE session_id = :s AND transcript_revision < :r "
        "AND state = 'active'"), {"s": session_id, "r": rev})
    await conn.execute(text("DELETE FROM claims WHERE session_id = :s AND transcript_revision = :r"),
                       {"s": session_id, "r": rev})  # claim_values cascade
    for c in claims:
        await conn.execute(text(
            f"INSERT INTO claims ({CLAIM_COLUMNS}, verifiable) VALUES (:id, :s, :r, :ct, :uk, :sl, :ns, :q, "
            "CAST(:qa AS jsonb), CAST(:also AS jsonb), :a, :b, CAST(:cond AS jsonb), :vag, :ec, :avg, :minn, false, :rd, "
            "'{}', 'active', :sup, :pv, :ver)"),
            {"id": c.id, "s": session_id, "r": rev, "ct": c.claim_type.value, "uk": c.utterance_kind.value,
             "sl": c.speaker_label, "ns": c.normalized_statement, "q": c.quote, "qa": _anchors_json(c.anchors),
             "also": _anchors_json(c.also_said_at), "a": c.start_ms, "b": c.end_ms,
             "cond": json.dumps([x.model_dump() for x in c.conditions]), "vag": c.vagueness,
             "ec": c.extraction_confidence, "avg": c.asr_avg_conf, "minn": c.asr_min_numeric_conf,
             "rd": readiness_of(c.claim_type), "sup": c.supersedes_claim_id, "pv": PROMPT_VERSION,
             # kind-based here; the speaker part (not a confirmed customer) is applied at read time
             "ver": c.utterance_kind in VERIFIABLE_KINDS})
        for v in c.values:
            await conn.execute(text(
                "INSERT INTO claim_values (id, claim_id, " + ", ".join(VALUE_FIELDS) + ") VALUES (:id, :c, "
                + ", ".join(f":{f}" for f in VALUE_FIELDS) + ")"),
                {"id": uuid7(), "c": c.id, **{f: getattr(v, f) for f in VALUE_FIELDS}})


async def load_claims(conn: AsyncConnection, session_id: uuid.UUID, *, claim_id: uuid.UUID | None = None,
                      rev: int | None = None) -> list[Claim]:
    where, params = "session_id = :s", dict[str, object]({"s": session_id})
    if claim_id is not None:
        where, params = where + " AND id = :id", {**params, "id": claim_id}
    if rev is not None:
        where, params = where + " AND transcript_revision = :r", {**params, "r": rev}
    rows = (await conn.execute(text(f"SELECT {CLAIM_COLUMNS} FROM claims WHERE {where} ORDER BY start_ms, id"),
                               params)).all()
    if not rows:
        return []
    vals = (await conn.execute(text(
        "SELECT claim_id, " + ", ".join(VALUE_FIELDS) + " FROM claim_values WHERE claim_id = ANY(:ids) ORDER BY id"),
        {"ids": [r.id for r in rows]})).all()
    by_claim: dict[uuid.UUID, list[ExtractedValue]] = {}
    for v in vals:
        by_claim.setdefault(v.claim_id, []).append(ExtractedValue.model_construct(**{f: getattr(v, f) for f in VALUE_FIELDS}))
    out = []
    for r in rows:
        anchors = [QuoteAnchor(**a) for a in r.quote_anchors]
        role = await resolve(conn, session_id, r.transcript_revision, r.speaker_label, anchors[0].segment_id)
        kind = UtteranceKind(r.utterance_kind)
        out.append(Claim(
            id=r.id, session_id=session_id, transcript_revision=r.transcript_revision, claim_type=r.claim_type,
            utterance_kind=kind, speaker_label=r.speaker_label, speaker_role=role.speaker_role,
            speaker_source=role.speaker_source, role_assignment=role.role_assignment,  # type: ignore[arg-type]
            normalized_statement=r.normalized_statement, quote=r.quote, quote_anchors=anchors, start_ms=r.start_ms,
            end_ms=r.end_ms, values=by_claim.get(r.id, []), conditions=[Condition(**c) for c in r.conditions or []],
            vagueness=r.vagueness, extraction_confidence=r.extraction_confidence, asr_avg_conf=r.asr_avg_conf,
            asr_min_numeric_conf=r.asr_min_numeric_conf, quote_confirmed_by_user=r.quote_confirmed_by_user,
            readiness=r.readiness, missing_inputs=list(r.missing_inputs or []),
            verifiable=kind in VERIFIABLE_KINDS and role.speaker_role != "customer", state=r.state,
            supersedes_claim_id=r.supersedes_claim_id, also_said_at=[QuoteAnchor(**a) for a in r.also_said_at or []],
            prompt_version=r.prompt_version))
    return out


def claim_event(c: Claim) -> dict[str, Any]:
    return json.loads(c.model_dump_json())  # type: ignore[no-any-return]
