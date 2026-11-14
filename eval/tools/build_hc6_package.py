"""T3.7: build the HC-6 rule review package from backend/data/rules/*.yaml and the rule fixtures.

For each MVP rule: instrument, source URL, retrieved verbatim fragments with paragraph ids, effective date,
applicability, en/hi explanation drafts, and shadow precision/recall on the fixtures (positive = "violates"), plus the
would-be outcome on the 10 synthetic agreements under stated assumed facts. Nothing is enabled; the HC-6 decision is a
Phase 4 human checkpoint.

Run from backend/:  PYTHONPATH=. uv run python ../eval/tools/build_hc6_package.py
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import yaml

from app.modules.rules.engine import RuleFacts, evaluate, load_rules
from app.schemas.claims import ExtractedValue

ROOT = Path(__file__).resolve().parents[1]
FIX = yaml.safe_load((ROOT.parent / "backend" / "tests" / "fixtures" / "rules_fixtures.yaml").read_text())["cases"]
PRODUCT = {"Home Loan": "home_loan", "Business Loan": "business_loan"}


def fixture_scores(rule: dict) -> tuple[int, int, int, int]:
    from tests.unit.test_verify_merge import claim

    tp = fp = fn = n = 0
    for f in (x for x in FIX if x["rule"] == rule["id"]):
        c = None
        if f["claim"]:
            c = claim(claim_type=f["claim"]["type"], values=[ExtractedValue(
                field=f["claim"]["type"], raw_text="1 percent", quantity="percent", value=Decimal(f["claim"]["value"]),
                normaliser_version="norm-1.0")])
        got = evaluate(rule, RuleFacts(facts=f["facts"], doc_kinds=set(f["doc_kinds"]), doc_text=f["doc_text"],
                                       doc_terms=f["doc_terms"], has_document=f["has_document"]), c).outcome
        n += 1
        tp += got == "violates" and f["expected"] == "violates"
        fp += got == "violates" and f["expected"] != "violates"
        fn += got != "violates" and f["expected"] == "violates"
    return tp, fp, fn, n


def agreement_facts(ag: str) -> RuleFacts:
    t = yaml.safe_load((ROOT / "datasets" / "agreements" / ag / "terms.yaml").read_text())
    business = t["product"] == "Business Loan"
    rate_type = next((x.get("rate_type") for x in t["terms"] if x["field"] == "interest_rate" and x.get("rate_type")), None)
    facts = {"loan_kind": "retail_or_msme_term", "sanction_date": "2026-02-01", "borrower_type": "individual",
             "purpose": "business" if business else "non_business", **({"rate_type": rate_type} if rate_type else {})}
    terms = [{"field": x["field"], "value": x["value"], "in_kfs": x["location"] == "kfs_table"} for x in t["terms"]]
    text = " ".join(str(x["raw_text"]) for x in t["terms"])
    return RuleFacts(facts=facts, doc_kinds={"kfs"}, doc_text=text, doc_terms=terms, has_document=True)


def main() -> None:
    today = dt.date.today().isoformat()
    rules = load_rules()
    L = [f"# HC-6 rule review package ({today})", "",
         "> For a qualified legal/compliance reviewer. **No rule is enabled** (`enabled: false`, `reviewed_by: null`); the "
         "rules run in shadow only and nothing reaches users. HC-6 approves each rule individually (doc 07 §2) in Phase 4. "
         "Quotes are verbatim fragments retrieved from the source URL on the date shown; the reviewer confirms the full "
         "paragraphs, applicability lists, exemptions and the en/hi wording. Fixtures and agreements are synthetic.", "",
         "Decision needed per rule: **approve as written / approve with changes / keep in shadow**. Approval records "
         "`reviewed_by` + `hc6_approved_at`; `rules.user_visible` also needs the startup check updated.", ""]
    for r in rules:
        tp, fp, fn, n = fixture_scores(r)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        L += [f"## {r['id']} (v{r['version']})", "", f"**{r['title']}**", "",
              f"- Instrument: {r['instrument']}", f"- Source: {r['source_url']} (retrieved {r['retrieved_on']})",
              f"- Effective from: {r['effective_from']}", f"- Applicability: {r['applicability_detail']}",
              f"- Engine applies when: `{r['applies_when']}`; checks: `{r['checks']}`", "", "Verbatim fragments:", ""]
        L += [f"> ¶{q['para']}: “{q['text']}”" for q in r["quote"]]
        L += ["", f"- Draft (en): {r['explain_en']}", f"- Draft (hi): {r['explain_hi']}",
              f"- Shadow on {n} fixtures (positive = violates): precision {prec:.2f}, recall {rec:.2f} (tp {tp}, fp {fp}, fn {fn})", ""]
    L += ["## Would-be outcomes on the synthetic agreements (shadow)", "",
          "Assumed facts (not from users): individual borrower, sanctioned 2026-02-01, non-business except AG07 "
          "(business loan); rate type as printed.", "", "| Agreement | " + " | ".join(r["id"] for r in rules) + " |",
          "|---|" + "---|" * len(rules)]
    for i in range(1, 11):
        ag = f"AG{i:02d}"
        rf = agreement_facts(ag)
        L.append(f"| {ag} | " + " | ".join(evaluate(r, rf).outcome for r in rules) + " |")
    L += ["", "## Known limitations", "",
          "- Fixture precision/recall measure the engine's logic on hand-written cases, not real documents.",
          "- `RBI-KFS-2024-APR-DISCLOSED` uses a text search for 'annual percentage rate'/'APR'; KFS layouts vary.",
          "- `RBI-PENAL-2023-NOT-INTEREST`: RBI later extended the timeline for existing loans; dates to confirm.",
          "- Facts such as borrower type and sanction date come only from the user (`PUT /sessions/{id}/facts`); missing "
          "facts give `applicability_unknown`, never a guess."]
    out = ROOT / "reports" / f"{today}_HC-6_rule_review_package.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out)
    print("\n".join(x for x in L if x.startswith("- Shadow") or x.startswith("| AG")))


if __name__ == "__main__":
    main()
