"""T3.5 shadow rules: every fixture (≥ 4 per rule), corpus safety (nothing enabled), relevance."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.modules.rules.engine import RuleFacts, evaluate, load_rules, user_visible
from app.schemas.claims import ExtractedValue
from tests.unit.test_verify_merge import claim

FIX = yaml.safe_load((Path(__file__).parents[1] / "fixtures" / "rules_fixtures.yaml").read_text())["cases"]
RULES = {r["id"]: r for r in load_rules()}


def test_corpus_is_shadow_only() -> None:
    assert len(RULES) == 5
    for r in RULES.values():
        assert r["enabled"] is False and r["reviewed_by"] is None and r["quote"] is None and not user_visible(r)
        assert r["source_url_verified"] is False and r["source_url"].startswith("https://www.rbi.org.in/")
        assert r["explain_en"] is None  # wording is written and reviewed in the HC-6 package (T3.7)


def test_at_least_four_fixtures_per_rule_covering_each_outcome() -> None:
    by = Counter(f["rule"] for f in FIX)
    assert set(by) == set(RULES) and min(by.values()) >= 4
    for rid in RULES:
        assert {f["expected"] for f in FIX if f["rule"] == rid} >= {"violates", "complies", "not_applicable", "unknown"}


@pytest.mark.parametrize("f", FIX, ids=[f"{f['rule']}:{f['name']}" for f in FIX])
def test_fixture(f: dict) -> None:
    c = None
    if f["claim"]:
        c = claim(claim_type=f["claim"]["type"], values=[ExtractedValue(
            field=f["claim"]["type"], raw_text="1 percent", quantity="percent", value=Decimal(f["claim"]["value"]),
            normaliser_version="norm-1.0")])
    rf = RuleFacts(facts=f["facts"], doc_kinds=set(f["doc_kinds"]), doc_text=f["doc_text"], doc_terms=f["doc_terms"],
                   has_document=f["has_document"])
    assert evaluate(RULES[f["rule"]], rf, c).outcome == f["expected"]
