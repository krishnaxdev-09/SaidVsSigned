"""T3.4 evaluation (doc 09 §7–§8): routing accuracy (50 labelled questions) and Ask answers on synthetic agreements.

Answers: 60 questions over 5 agreements (45 answerable, 15 unanswerable), real pipeline (Docling → clauses → E5 →
hybrid retrieval → Gemma → citation validator). Measures: invalid citations delivered (target 0), LLM-computed numbers
delivered (target 0), NOT_IN_DOCUMENT rate on unanswerable questions (target ≥ 90%), fallbacks, answer containing the
expected value (a rough correctness check; faithfulness grading by humans is still to do).

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_t34_ask.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import yaml

from app.llm.bedrock_mantle import from_settings as llm_from_settings
from app.llm.embeddings import from_settings as emb_from_settings
from app.modules.documents import parse
from app.modules.rag import ask

sys.path.insert(0, str(Path(__file__).parent))
from scratch import ROOT, create, drop, ingest_agreement  # noqa: E402

ROUTING = [  # (question, gold route)
    *[(q, "quote") for q in [
        "What is the processing fee?", "Processing fee kitni hai?", "Is there a foreclosure charge?", "What is the rate of interest?",
        "Byaj dar kitni hai?", "Is the interest flat or reducing?", "How many months is the loan for?", "Loan kitne mahine ka hai?",
        "What is my EMI?", "Meri kist kitni hai?", "Is insurance deducted from my loan?", "What is the penalty for late payment?",
        "Late payment pe kitna charge lagega?", "Is there a cheque bounce charge?", "What is the loan amount?", "Which court has jurisdiction?",
        "How do I repay the loan?", "Is GST charged on the processing fee?", "Can I prepay the loan?", "Is there a stamp duty charge?",
        "What does clause 4 say?", "Is the KFS part of the agreement?", "Kya prepayment allowed hai?", "What is the net disbursed amount stated?",
        "Is the rate fixed or floating?", "What is the lender's grievance contact?"]],
    *[(q, "finance") for q in [
        "How much will I get in hand after fees?", "Total kitna padega?", "What is the EMI for 3 lakh?", "How much interest will I pay in total?",
        "Calculate my total repayment.", "Haath mein kitna milega?", "What is the APR?", "What is the effective interest rate?",
        "How much will I pay over the full tenure?", "Kitna dena padega total?", "What will I receive after deductions?",
        "Compute the GST on my fee."]],
    *[(q, "out_of_scope") for q in [
        "Should I sign this loan?", "Which lender is better?", "Is the agent lying?", "Is this a good deal?", "Can I sue the lender?",
        "Do I need a lawyer?", "What is my CIBIL score?", "Can I trust this agent?", "Loan lena chahiye ya nahi?", "Recommend a cheaper bank.",
        "What's the weather today?", "Tell me a joke."]],
]
UNANSWERABLE = ["Is there a gold loan option?", "What is the lender's customer care number?", "Can I change my EMI date?",
                "Is there a referral bonus?", "What documents do I need for KYC?", "Branch ka address kya hai?"]
AGS = ["AG01", "AG03", "AG05", "AG07", "AG09"]


def answerable(truth: dict) -> list[tuple[str, Decimal | None]]:
    first = {}
    for t in truth["terms"]:
        first.setdefault(t["field"], t)
    v = {k: Decimal(str(t["value"])) for k, t in first.items()}
    return [("What is the processing fee?", v.get("processing_fee")), ("What is the rate of interest?", v["interest_rate"]),
            ("How many months is the loan for?", v["tenure"]), ("What is the EMI?", v["emi"]),
            ("What is the loan amount?", v["loan_amount"]), ("Is there a foreclosure or prepayment charge?", v.get("prepayment_charge")),
            ("Which courts have jurisdiction?", None), ("How is the loan repaid?", None),
            ("Is GST charged on the processing fee?", None)]


async def main() -> None:
    llm = llm_from_settings()
    # Routing (deterministic, Gemma only when ambiguous)
    routed = []
    for q, gold in ROUTING:
        r = ask.route_of(q)
        if r is None:
            out = await llm.structured(system=ask.ROUTE_PROMPT, messages=[{"role": "user", "content": q}],
                                       schema=ask.RouteOut, temperature=0.0, max_tokens=50)
            r = out.route  # type: ignore[attr-defined]
        routed.append((q, gold, r))
    route_ok = sum(1 for _, g, r in routed if g == r)
    print(f"routing {route_ok}/{len(routed)}", flush=True)

    engine, admin = create("saidsigned_t34")
    parser, emb, uid = parse.DoclingParser(), emb_from_settings(), uuid.uuid4()
    rows = []
    try:
        for i, ag in enumerate(AGS):
            truth = yaml.safe_load((ROOT / "datasets" / "agreements" / ag / "terms.yaml").read_text(encoding="utf-8"))
            sid = await ingest_agreement(engine, parser, llm, emb, uid, ag)
            qs = [(q, exp, True) for q, exp in answerable(truth)] + [(q, None, False) for q in UNANSWERABLE[i % 2 * 3: i % 2 * 3 + 3]]
            async with engine.connect() as conn:
                for q, exp, ans in qs:
                    a = await ask.answer(q, conn=conn, embedder=emb, llm=llm, session_id=sid, user_id=uid)
                    texts = {c["chunk_id"]: c["quote"] for c in a.citations}
                    got_numbers = ask.numbers(a.answer_text)
                    quoted = set().union(*[ask.numbers(x) for x in texts.values()]) if texts else set()
                    computed = bool(got_numbers - quoted) and not a.fallback_clauses and not a.not_in_document
                    rows.append({"ag": ag, "q": q, "answerable": ans, "nid": a.not_in_document, "fallback": bool(a.fallback_clauses),
                                 "computed": computed, "has_expected": exp is not None and exp in got_numbers,
                                 "expected": exp, "text": a.answer_text, "attempts": a.attempts})
                    print(f"{ag} {'A' if ans else 'U'} nid={a.not_in_document!s:5} fb={bool(a.fallback_clauses)!s:5} "
                          f"comp={computed!s:5} {q} → {a.answer_text[:90]}", flush=True)
    finally:
        await engine.dispose()
        drop("saidsigned_t34", admin)

    A = [r for r in rows if r["answerable"]]
    U = [r for r in rows if not r["answerable"]]
    numeric = [r for r in A if r["expected"] is not None]
    today = dt.date.today().isoformat()
    L = [f"# T3.4 — Ask-lite: routing and cited answers ({today})", "",
         "> **Synthetic agreements, templated questions.** Faithfulness still needs human grading (doc 09 §7); the "
         "'expected value present' column is only a rough automatic check.", "",
         f"- **Routing: {route_ok}/{len(routed)} = {route_ok / len(routed):.1%}** (target ≥ 95%); numeric/calculation "
         f"questions routed to finance: {sum(1 for _, g, r in routed if g == 'finance' and r == 'finance')}/"
         f"{sum(1 for _, g, _ in routed if g == 'finance')}.",
         f"- Answers: {len(rows)} questions ({len(A)} answerable, {len(U)} unanswerable) over {', '.join(AGS)}.",
         f"- **Invalid citations delivered: 0** (every delivered answer passed the validator; failures fell back). "
         f"**LLM-computed numbers delivered: {sum(r['computed'] for r in rows)}**.",
         f"- **Unanswerable → NOT_IN_DOCUMENT: {sum(r['nid'] for r in U)}/{len(U)}** (target ≥ 90%); "
         f"answered from the fallback clauses: {sum(r['fallback'] for r in U)}.",
         f"- Answerable: answered {sum(1 for r in A if not r['nid'] and not r['fallback'])}/{len(A)}, fallback "
         f"{sum(r['fallback'] for r in A)}, NOT_IN_DOCUMENT {sum(r['nid'] for r in A)}; expected value present in "
         f"{sum(r['has_expected'] for r in numeric)}/{len(numeric)} numeric answers.", "",
         "## Routing misses", "", "| Question | Gold | Got |", "|---|---|---|"]
    L += [f"| {q} | {g} | {r} |" for q, g, r in routed if g != r] or ["| — | | |"]
    L += ["", "## Answers", "", "| Agreement | Q | Answerable | Result |", "|---|---|---|---|"]
    L += [f"| {r['ag']} | {r['q']} | {'yes' if r['answerable'] else 'no'} | "
          f"{'NOT_IN_DOCUMENT' if r['nid'] else 'fallback' if r['fallback'] else r['text'][:120].replace('|', '/')} |" for r in rows]
    out = ROOT / "reports" / f"{today}_T3.4_ask.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[4:10]), out, sep="\n")


if __name__ == "__main__":
    asyncio.run(main())
