"""T2.6 evaluation (doc 05 §9): contract engine confusion matrix on 200 labelled claim–clause pairs.

The 10 synthetic agreements are ingested with the real pipeline (Docling → segment → lexicon + Gemma doc_terms → E5
chunks) into a scratch database `saidsigned_t26` (dropped after). For each agreement 20 claims are built from its
terms.yaml with a gold status set by construction from what the document says (same value → MATCH, different →
CONFLICT, rounding band → UNCLEAR, unit change → NOT_VERIFIED(calculation_not_enabled), …). Claims are run through
the contract engine (Gemma judge + NLI on the semantic path) + finance stub → gates → merge, as a user-confirmed
agent with high ASR confidence and a confirmed quote, so the matrix measures the contract engine itself.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_t26_contract.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import subprocess
import sys
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.llm.bedrock_mantle import from_settings as llm_from_settings
from app.llm.embeddings import from_settings as emb_from_settings
from app.llm.nli import LocalNLI
from app.modules.contract.engine import ContractEngine
from app.modules.documents import ingest, parse, segment, terms
from app.modules.rag import retrieve
from app.modules.verify import gates
from app.modules.verify.engines import FinanceEngine
from app.modules.verify.merge import merge
from app.modules.verify.service import EngineContext, load_thresholds, run_engines, spoken_evidence
from app.schemas.claims import Claim, Condition, ExtractedValue
from app.schemas.verification import QuoteAnchor, Status

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend"
SCRATCH = "saidsigned_t26"
D = Decimal


def mk(ctype: str, statement: str, *, raw: str | None = None, quantity: str = "percent", value: Decimal | None = None,
       kind: str = "assertion", conditions: list[Condition] | None = None, **vals: Any) -> Claim:
    values = [] if raw is None else [ExtractedValue(field=ctype, raw_text=raw, quantity=quantity, value=value,  # type: ignore[arg-type]
                                                    normaliser_version="norm-1.0", **vals)]
    return Claim(id=uuid.uuid4(), session_id=uuid.uuid4(), transcript_revision=1, claim_type=ctype,  # type: ignore[arg-type]
                 utterance_kind=kind, speaker_label="speaker_0", speaker_role="agent",  # type: ignore[arg-type]
                 speaker_source="diarized", role_assignment="user_confirmed", normalized_statement=statement,
                 quote=statement, quote_anchors=[QuoteAnchor(segment_id=uuid.uuid4(), transcript_revision=1,
                                                             start_char=0, end_char=len(statement))],
                 start_ms=0, end_ms=1000, values=values, conditions=conditions or [], vagueness="specific",
                 extraction_confidence=0.9, asr_avg_conf=0.99, asr_min_numeric_conf=0.99, quote_confirmed_by_user=True,
                 readiness="needs_document", verifiable=True, prompt_version="eval")


def inr(x: Decimal) -> str:
    return f"Rs. {int(x)}"


def pairs_for(truth: dict) -> list[tuple[str, Claim, str]]:
    """(id, claim, gold status[:reason]) — gold from what the document says."""
    first: dict[str, dict] = {}
    for t in truth["terms"]:
        first.setdefault(t["field"], t)
    traps = {t["id"] for t in truth["traps"]}
    v = {k: D(str(t["value"])) for k, t in first.items()}
    out: list[tuple[str, Claim, str]] = []

    def add(pid: str, c: Claim, gold: str) -> None:
        out.append((pid, c, gold))

    fee = first.get("processing_fee")
    if fee and fee["quantity"] == "percent":
        f = v["processing_fee"]
        add("fee_same", mk("processing_fee", f"The processing fee is {f} percent.", raw=f"{f} percent", value=f), "MATCH")
        add("fee_diff", mk("processing_fee", f"The processing fee is {f + 1} percent.", raw=f"{f + 1} percent",
                           value=f + 1), "CONFLICT")
        add("fee_round", mk("processing_fee", f"The processing fee is {f + D('0.2')} percent.",
                            raw=f"{f + D('0.2')} percent", value=f + D("0.2")), "UNCLEAR:possible_rounding")
        add("fee_inr", mk("processing_fee", "The processing fee is Rs. 5000.", raw="Rs. 5000", quantity="amount_inr",
                          value=D(5000)), "NOT_VERIFIED:calculation_not_enabled")
        gst_gold = "UNCLEAR:assumption_dependent" if fee.get("includes_gst") is False else "MATCH"
        add("fee_incl_gst", mk("processing_fee", f"The processing fee is {f} percent including GST.",
                               raw=f"{f} percent", value=f, includes_gst=True), gst_gold)
    elif fee:
        f = v["processing_fee"]
        add("fee_same", mk("processing_fee", f"The processing fee is {inr(f)}.", raw=inr(f), quantity="amount_inr",
                           value=f), "MATCH")
        add("fee_diff", mk("processing_fee", f"The processing fee is {inr(f * 2)}.", raw=inr(f * 2),
                           quantity="amount_inr", value=f * 2), "CONFLICT")
        add("fee_round", mk("processing_fee", f"The processing fee is {inr(f * D('1.02'))}.", raw=inr(f * D("1.02")),
                            quantity="amount_inr", value=D(int(f * D("1.02")))), "UNCLEAR:possible_rounding")
        add("fee_pct", mk("processing_fee", "The processing fee is 1 percent.", raw="1 percent", value=D(1)),
            "NOT_VERIFIED:calculation_not_enabled")
        add("fee_zero", mk("processing_fee", "There is no processing fee.", raw="no processing fee",
                           quantity="amount_inr", value=D(0)), "CONFLICT")
    r = v["interest_rate"]
    add("rate_same", mk("interest_rate", f"The interest rate is {r} percent per annum.", raw=f"{r} percent", value=r),
        "MATCH")
    add("rate_diff", mk("interest_rate", f"The interest rate is {r - 2} percent per annum.", raw=f"{r - 2} percent",
                        value=r - 2), "CONFLICT")
    flat = "flat_rate_small_print" in traps
    add("rate_basis", mk("interest_rate", f"The interest rate is {r} percent on a {'reducing' if flat else 'flat'} basis.",
                         raw=f"{r} percent", value=r, rate_basis="reducing" if flat else "flat"), "CONFLICT")
    add("rate_range", mk("interest_rate", f"The interest will be around {r - 1} to {r + 1} percent.",
                         raw=f"{r - 1} to {r + 1} percent", value_low=r - 1, value_high=r + 1), "MATCH")
    n = v["tenure"]
    add("tenure_diff", mk("tenure", f"The loan is for {n + 12} months.", raw=f"{n + 12} months", quantity="months",
                          value=n + 12), "CONFLICT")
    e = v["emi"]
    add("emi_same", mk("emi", f"Your EMI will be {inr(e)}.", raw=inr(e), quantity="amount_inr", value=e), "MATCH")
    add("emi_diff", mk("emi", f"Your EMI will be {inr(e * D('0.85'))}.", raw=inr(e * D("0.85")), quantity="amount_inr",
                       value=D(int(e * D("0.85")))), "CONFLICT")
    a = v["loan_amount"]
    big = D(int(a * D("1.2")))  # outside the ±3% rounding band (doc 06 §5)
    add("amount_diff", mk("loan_amount", f"Your loan of {inr(big)} is approved.", raw=inr(big),
                          quantity="amount_inr", value=big), "CONFLICT")
    prepay_free = v.get("prepayment_charge", D(0)) == 0
    add("prepay_zero", mk("prepayment_charge", "There is no foreclosure charge at all.", raw="no foreclosure charge",
                          quantity="amount_inr", value=D(0)), "MATCH" if prepay_free else "CONFLICT")
    add("prepay_cond", mk("prepayment_charge", "If you prepay after 12 EMIs there is no charge.", raw="no charge",
                          quantity="amount_inr", value=D(0), kind="conditional",
                          conditions=[Condition(text="after 12 EMIs", kind="time_bound")]),
        "MATCH" if prepay_free else "UNCLEAR:condition_mismatch")
    if "insurance_bundled" in v:
        add("ins_zero", mk("insurance_bundled", "There is no insurance charge.", raw="no insurance charge",
                           quantity="amount_inr", value=D(0)), "CONFLICT")
    else:
        add("ins_zero", mk("insurance_bundled", "There is no insurance charge.", raw="no insurance charge",
                           quantity="amount_inr", value=D(0)), "NOT_VERIFIED:clause_not_found")
    # Semantic pairs (no comparable value): NLI + Gemma judge.
    add("sem_nach", mk("general_assurance", "The EMI is paid by NACH mandate on the 5th of each month."), "MATCH")
    add("sem_cash", mk("general_assurance", "You can pay the EMI in cash at the branch every week."), "CONFLICT")
    add("sem_court", mk("general_assurance", "Courts at Delhi have jurisdiction over this agreement."), "CONFLICT")
    add("sem_kfs", mk("document_assurance", "The Key Fact Statement is part of the loan agreement."), "MATCH")
    assert len(out) == 20, len(out)
    return out


async def main() -> None:
    base = os.environ["DATABASE_URL"]
    admin = base.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {SCRATCH}")
    url = base.rsplit("/", 1)[0] + "/" + SCRATCH
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                   env={**os.environ, "DATABASE_URL": url}, check=True, capture_output=True)
    engine = create_async_engine(url)
    parser, llm, emb, nli = parse.DoclingParser(), llm_from_settings(), emb_from_settings(), LocalNLI()
    contract = ContractEngine(embedder=emb, llm=llm, nli=nli)
    th = load_thresholds()
    uid = uuid.uuid4()
    rows: list[dict] = []
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO users (id, email, password_hash) VALUES (:u, 't26@example.com', 'x')"), {"u": uid})
    for i in range(10):
        ag = f"AG{i + 1:02d}"
        pdf = ROOT / "datasets" / "agreements" / ag / "agreement.pdf"
        truth = yaml.safe_load((pdf.parent / "terms.yaml").read_text(encoding="utf-8"))
        d = parser.parse(pdf, ocr=False, force_full_page_ocr=False)
        parse.confidence_pass(d, set())
        clauses = segment.segment(d.items)
        lex = terms.lexicon_terms(clauses)
        llm_terms, _ = await terms.llm_terms(llm, clauses, {t.clause_ord for t in lex})
        chunks = [(c, piece) for c in clauses for piece in retrieve.chunk_texts(c.text)]
        vecs = await emb.embed([((c.section_path + "\n") if c.section_path else "") + p for c, p in chunks], kind="passage")
        sid, did = uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO sessions (id, owner_id, mode, state, retention_until) VALUES "
                                    "(:s, :u, 'upload', 'ready', now() + interval '1 day')"), {"s": sid, "u": uid})
            await conn.execute(text("INSERT INTO documents (id, session_id, kind, source, version, content_sha256, status) "
                                    "VALUES (:d, :s, 'loan_agreement', 'pdf', 1, :h, 'ready')"), {"d": did, "s": sid, "h": ag})
            ids = await ingest._write_clauses(SimpleNamespace(conn=conn), did, 1, clauses, lex + llm_terms)  # type: ignore[arg-type]
            for (c, piece), vec in zip(chunks, vecs, strict=True):
                await conn.execute(text(
                    "INSERT INTO chunks (id, document_id, version, clause_id, page, section_path, text, embedding, "
                    "embedding_model) VALUES (:id, :d, 1, :c, :p, :sp, :t, CAST(:e AS vector), :m)"),
                    {"id": uuid.uuid4(), "d": did, "c": ids[c.ord], "p": c.page_start, "sp": c.section_path, "t": piece,
                     "e": "[" + ",".join(f"{x:.6f}" for x in vec) + "]", "m": emb.name})
        async with engine.connect() as conn:
            ctx = EngineContext(conn=conn, session_id=sid, owner_id=uid, thresholds=th)
            for pid, claim, gold in pairs_for(truth):
                outs = await run_engines([contract, FinanceEngine()], claim, ctx)
                for o in outs:
                    o.verdict = gates.apply_gates(o.verdict, claim, th, hindi=False)
                m = merge(claim, spoken_evidence(claim), outs)
                got = m.status.value + (f":{m.reason_code.value}" if m.reason_code and ":" in gold else "")
                rows.append({"ag": ag, "pair": pid, "gold": gold, "got": got,
                             "gold_status": gold.split(":")[0], "got_status": m.status.value})
                print(f"{ag} {pid:13s} gold={gold:38s} got={m.status.value}:{m.reason_code.value if m.reason_code else ''}"
                      f"{'' if got == gold else '   <-- MISS'}", flush=True)
    await engine.dispose()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")

    statuses = [s.value for s in Status]
    cm = Counter((r["gold_status"], r["got_status"]) for r in rows)
    tp_c = cm[("CONFLICT", "CONFLICT")]
    pred_c = sum(cm[(g, "CONFLICT")] for g in statuses)
    exact = sum(1 for r in rows if r["got"] == r["gold"])
    status_acc = sum(1 for r in rows if r["got_status"] == r["gold_status"])
    today = dt.date.today().isoformat()
    L = [f"# T2.6 — Contract engine confusion matrix ({today})", "",
         "> **Synthetic agreements and constructed claims.** 200 claim–clause pairs (20 per synthetic agreement) with "
         "gold statuses set by construction from terms.yaml. Claims are passed as a user-confirmed agent with high ASR "
         "confidence and a confirmed quote, so the matrix isolates the contract engine (gates are tested separately). "
         "Real calls and lender documents will be harder; not the Phase 4 measurement.", "",
         f"- Pairs: {len(rows)}. Status accuracy **{status_acc}/{len(rows)} ({100 * status_acc / len(rows):.1f}%)**; "
         f"status + reason code exact: {exact}/{len(rows)}.",
         f"- **CONFLICT precision {tp_c}/{pred_c} = {tp_c / pred_c if pred_c else 0:.3f}** (target ≥ 0.90, doc 12 A.3); "
         f"CONFLICT recall {tp_c}/{sum(cm[('CONFLICT', s)] for s in statuses)}.", "",
         "| gold \\ predicted | " + " | ".join(statuses) + " |", "|---|" + "---|" * len(statuses)]
    for g in statuses:
        L.append(f"| **{g}** | " + " | ".join(str(cm[(g, p)]) for p in statuses) + " |")
    L += ["", "## Misses", "", "| Agreement | Pair | Gold | Got |", "|---|---|---|---|"]
    L += [f"| {r['ag']} | {r['pair']} | {r['gold']} | {r['got']} |" for r in rows if r["got"] != r["gold"]] or ["| — | none | | |"]
    by = Counter(r["pair"] for r in rows)
    ok = Counter(r["pair"] for r in rows if r["got"] == r["gold"])
    L += ["", "## By pair type", "", "| Pair | n | correct |", "|---|---|---|"] + [f"| {p} | {n} | {ok[p]} |" for p, n in by.items()]
    out = ROOT / "reports" / f"{today}_T2.6_contract_confusion.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[5:14]), out, sep="\n")


if __name__ == "__main__":
    asyncio.run(main())
