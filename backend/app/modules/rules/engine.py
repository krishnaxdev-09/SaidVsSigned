"""Rules (shadow) and public-data engines — doc 07 (T3.5).

Rules: the cited RBI rule subset in `data/rules/*.yaml` always runs, but no rule is enabled (HC-6 per rule, never
assumed): the engine returns NOT_VERIFIED(rule_not_enabled) to the orchestrator — excluded from merge, UI, events and
reports — and stores the would-be outcome in `shadow_verdicts` for evaluation. A missing fact →
`applicability_unknown` (never guessed). Facts come from the session (`facts`, product type) and the documents.

Public data (demo only): lender identity against ≤ 5 curated FICTIONAL demo lenders (pg_trgm word similarity ≥ 0.6).
Found → a verdict the doc 08 gate caps at UNCLEAR(public_data_only); not found → NOT_VERIFIED(entity_not_found).
Public data never yields CONFLICT.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal

import yaml
from sqlalchemy import text

from app.core.config import BACKEND_DIR
from app.modules.verify.merge import Outcome
from app.modules.verify.service import EngineContext
from app.schemas.claims import Claim
from app.schemas.verification import EngineVerdict, FindingKind, PublicDataEvidence, ReasonCode, Status
from app.schemas.wording import REASON_TEXT

RULES_DIR = BACKEND_DIR / "data" / "rules"
RETAIL = {"personal_loan", "home_loan", "vehicle_loan", "business_loan", "gold_loan", "other_loan"}
Outcome_ = Literal["violates", "complies", "not_applicable", "unknown"]


@lru_cache
def load_rules() -> list[dict[str, Any]]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(RULES_DIR.glob("*.yaml"))]


def user_visible(rule: dict[str, Any]) -> bool:
    """A rule is user-visible only with enabled + a named reviewer + a verified quote (HC-6). None qualify in the MVP."""
    return bool(rule.get("enabled") and rule.get("reviewed_by") and rule.get("quote"))


@dataclass
class RuleFacts:
    facts: dict[str, Any] = field(default_factory=dict)  # borrower_type, purpose, rate_type, sanction_date, loan_kind
    doc_kinds: set[str] = field(default_factory=set)
    doc_text: str = ""
    doc_terms: list[dict[str, Any]] = field(default_factory=list)  # {field, value, in_kfs}
    has_document: bool = False


@dataclass
class RuleResult:
    rule_id: str
    version: int
    outcome: Outcome_
    missing: list[str] = field(default_factory=list)
    detail: str = ""


def _fact_ok(key: str, want: Any, facts: dict[str, Any]) -> bool | None:
    if key.endswith("_gte"):
        v = facts.get(key[:-4])
        return None if v is None else date.fromisoformat(str(v)) >= date.fromisoformat(str(want))
    v = facts.get(key)
    return None if v is None else v == want


def evaluate(rule: dict[str, Any], rf: RuleFacts, claim: Claim | None = None) -> RuleResult:
    rid, ver = rule["id"], int(rule.get("version", 1))
    missing, applicable = [], True
    for key, want in (rule.get("applies_when") or {}).items():
        ok = _fact_ok(key, want, rf.facts)
        if ok is None:
            missing.append(key.removesuffix("_gte"))
        elif not ok:
            applicable = False
    if not applicable:
        return RuleResult(rid, ver, "not_applicable")
    if missing:
        return RuleResult(rid, ver, "unknown", missing)
    for chk in rule.get("checks", []):
        exp, target = chk.get("expect", {}), chk.get("target")
        if target == "session":
            if not rf.has_document:
                return RuleResult(rid, ver, "unknown", ["document"])
            if exp.get("document_kind_present") not in rf.doc_kinds:
                return RuleResult(rid, ver, "violates", detail=f"no {exp['document_kind_present']} uploaded")
        elif target == "doc_text":
            if not rf.has_document:
                return RuleResult(rid, ver, "unknown", ["document"])
            low = rf.doc_text.lower()
            if "contains_any" in exp and not any(w in low for w in exp["contains_any"]):
                return RuleResult(rid, ver, "violates", detail=f"none of {exp['contains_any']} in the documents")
            if "not_contains_any" in exp and any(w in low for w in exp["not_contains_any"]):
                return RuleResult(rid, ver, "violates", detail=f"documents mention {exp['not_contains_any']}")
        elif target == "doc_term":
            terms = [t for t in rf.doc_terms if t["field"] in chk.get("fields", [])]
            if "in_kfs_table" in exp and any(not t["in_kfs"] for t in terms):
                kfs_fields = {t["field"] for t in terms if t["in_kfs"]}
                if any(t["field"] not in kfs_fields for t in terms):
                    return RuleResult(rid, ver, "violates", detail="a charge appears outside the KFS table")
            if "value_eq" in exp and any(t["value"] is not None and Decimal(str(t["value"])) != Decimal(str(exp["value_eq"]))
                                         for t in terms):
                return RuleResult(rid, ver, "violates", detail=f"document term ≠ {exp['value_eq']}")
        elif target == "claim" and claim is not None and claim.claim_type.value in chk.get("claim_types", []):
            vals = [v.value for v in claim.values if v.value is not None]
            if "value_eq" in exp and any(x != Decimal(str(exp["value_eq"])) for x in vals):
                return RuleResult(rid, ver, "violates", detail=f"spoken value ≠ {exp['value_eq']}")
    return RuleResult(rid, ver, "complies")


def relevant(rule: dict[str, Any], claim: Claim) -> bool:
    ct = claim.claim_type.value
    for chk in rule.get("checks", []):
        if chk.get("target") == "claim" and ct in chk.get("claim_types", []):
            return True
        if chk.get("target") == "doc_term" and ct in chk.get("fields", []):
            return True
        if chk.get("target") in ("session", "doc_text") and ct in ("document_assurance", "general_assurance",
                                                                  "interest_rate", "penal_charge"):
            return True
    return False


async def load_facts(ctx: EngineContext) -> RuleFacts:
    s = (await ctx.conn.execute(text("SELECT product_type, facts FROM sessions WHERE id = :s"), {"s": ctx.session_id})).first()
    facts = dict((s.facts or {}) if s else {})
    if s and s.product_type:
        facts.setdefault("loan_kind", "retail_or_msme_term" if s.product_type in RETAIL else None)
        facts.setdefault("purpose", "business" if s.product_type == "business_loan" else "non_business")
    docs = (await ctx.conn.execute(text(
        "SELECT d.kind FROM documents d WHERE d.session_id = :s AND d.status = 'ready' AND d.deleted_at IS NULL"),
        {"s": ctx.session_id})).scalars().all()
    rows = (await ctx.conn.execute(text(
        "SELECT c.text, c.is_table_row, t.field, t.value, t.rate_type FROM clauses c "
        "JOIN documents d ON d.id = c.document_id AND d.version = c.version "
        "LEFT JOIN doc_terms t ON t.clause_id = c.id WHERE d.session_id = :s AND d.status = 'ready' "
        "AND d.deleted_at IS NULL ORDER BY c.ord"), {"s": ctx.session_id})).all()
    rate_types = {r.rate_type for r in rows if r.field == "interest_rate" and r.rate_type in ("fixed", "floating")}
    if len(rate_types) == 1:
        facts.setdefault("rate_type", rate_types.pop())
    return RuleFacts(facts={k: v for k, v in facts.items() if v is not None}, doc_kinds=set(docs),
                     doc_text="\n".join(dict.fromkeys(r.text for r in rows)), has_document=bool(docs),
                     doc_terms=[{"field": r.field, "value": r.value, "in_kfs": bool(r.is_table_row)} for r in rows if r.field])


class RulesEngine:
    name: Literal["rules"] = "rules"
    shadow = True

    def applies(self, claim: Claim) -> bool:
        return any(relevant(r, claim) for r in load_rules())

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome:
        rf = await load_facts(ctx)
        for rule in load_rules():
            if not relevant(rule, claim):
                continue
            res = evaluate(rule, rf, claim)
            await ctx.conn.execute(text(
                "INSERT INTO shadow_verdicts (id, claim_id, engine, rule_id, verdict) VALUES (:id, :c, 'rules', :r, "
                "CAST(:v AS jsonb))"), {"id": uuid.uuid4(), "c": claim.id, "r": rule["id"], "v": json.dumps({
                    "version": res.version, "outcome": res.outcome, "missing": res.missing, "detail": res.detail,
                    "would_be": {"violates": "CONFLICT", "complies": "MATCH", "unknown": "NOT_VERIFIED",
                                 "not_applicable": "NOT_VERIFIED"}[res.outcome], "user_visible": user_visible(rule)})})
        return Outcome(EngineVerdict(engine="rules", status=Status.NOT_VERIFIED, reason_code=ReasonCode.RULE_NOT_ENABLED,
                                     finding_kind=None, reason=REASON_TEXT[ReasonCode.RULE_NOT_ENABLED], evidence=[]),
                       shadow=True)


DEMO_NOTE = "Demo data — general published information, not your sanctioned terms."


class PublicDataEngine:
    name: Literal["public_data"] = "public_data"
    shadow = False

    def applies(self, claim: Claim) -> bool:
        return claim.claim_type.value == "lender_identity"

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome:
        named = (await ctx.conn.execute(text("SELECT lender_name_raw FROM sessions WHERE id = :s"),
                                        {"s": ctx.session_id})).scalar()
        best = None
        for q in [x for x in (claim.quote, named) if x]:
            row = (await ctx.conn.execute(text(
                "SELECT id, name, category, source_url, source_date, word_similarity(normalized_name, lower(:q)) AS sim "
                "FROM public_entities WHERE demo_only ORDER BY sim DESC LIMIT 1"), {"q": q})).first()
            if row is not None and row.sim >= 0.6 and (best is None or row.sim > best.sim):
                best = row
        if best is None:
            return Outcome(EngineVerdict(engine="public_data", status=Status.NOT_VERIFIED,
                                         reason_code=ReasonCode.ENTITY_NOT_FOUND, finding_kind=None,
                                         reason=REASON_TEXT[ReasonCode.ENTITY_NOT_FOUND], evidence=[]))
        ev = PublicDataEvidence(entity_id=best.id, source_url=best.source_url, captured_at=best.source_date,
                                summary=f"{best.name} is in the demo lender list ({best.category}). {DEMO_NOTE}")
        return Outcome(EngineVerdict(engine="public_data", status=Status.MATCH, reason_code=None,
                                     finding_kind=FindingKind.PUBLIC, reason=ev.summary, evidence=[ev],
                                     suggested_question="Check the lender's registration on the regulator's website."))
