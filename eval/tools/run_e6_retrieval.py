"""E6 retrieval (doc 09 §7, doc 12 A.3): Recall@5 and MRR for BM25 vs vector vs hybrid, and T_ret calibration.

Real pipeline on the 10 synthetic agreements (digital PDFs): Docling → segment → lexicon + Gemma doc_terms → E5
embeddings (ADR-005 working choice) → Postgres FTS + pgvector, in a scratch database `saidsigned_e6` (dropped after).
150 answerable questions (15 per agreement, English and Hinglish alternating, templated by topic) with gold clauses
chosen by topic patterns over the segmented clauses; 30 unanswerable questions for the T_ret calibration.
A question is a hit when any gold clause is in the top 5.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_e6_retrieval.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.llm.bedrock_mantle import from_settings as llm_from_settings
from app.llm.embeddings import from_settings as emb_from_settings
from app.modules.documents import ingest, parse, segment, terms
from app.modules.rag import retrieve

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend"
SCRATCH = "saidsigned_e6"

# topic → (gold clause pattern, [English question, Hinglish question])
TOPICS: dict[str, tuple[str, list[str]]] = {
    "processing_fee": (r"processing fee", ["What is the processing fee?", "Processing fee kitni lagegi?"]),
    "file_charge": (r"processing fee", ["Is there any login fee or file charge?", "File charge kitna hai?"]),
    "interest": (r"rate of interest|interest is payable at|flat basis", ["What is the rate of interest?",
                                                                         "Byaj dar kitni hai?"]),
    "rate_basis": (r"rate of interest|flat basis|reducing", ["Is the interest flat or reducing?",
                                                              "Interest flat hai ya reducing?"]),
    "emi": (r"\| emi \||^emi", ["How much is my monthly EMI?", "Meri kist kitni hai?"]),
    "tenure": (r"tenure|equated monthly instalments", ["For how many months is the loan?", "Loan kitne mahine ka hai?"]),
    "prepayment": (r"prepay", ["Is there a foreclosure charge?", "Loan jaldi band karne pe charge lagega?"]),
    "late": (r"penal|late-payment|delay", ["What happens if I pay an EMI late?", "Late payment pe kitni penalty hai?"]),
    "net": (r"net disbursed|deducted from the disbursement|deducted from the loan amount",
            ["How much will I get in hand?", "Haath mein kitna paisa milega?"]),
    "total": (r"total amount payable", ["What is the total amount I will repay?", "Total kitna chukana padega?"]),
    "amount": (r"loan amount|agrees to borrow", ["What is the sanctioned loan amount?", "Loan amount kitna hai?"]),
    "insurance": (r"insurance|credit-shield", ["Is insurance deducted from my loan?", "Kya bima ka paisa katega?"]),
    "documentation": (r"documentation and stamping", ["Is there a documentation or stamp charge?",
                                                      "Stamp duty ka charge hai kya?"]),
    "bounce": (r"bounce", ["What is the cheque bounce charge?", "ECS bounce hone pe kitna lagega?"]),
    "repayment": (r"nach|repay the loan in", ["How do I repay the loan?", "EMI kaise bharni hai, NACH se?"]),
    "law": (r"governing law|jurisdiction", ["Which court has jurisdiction?", "Kaunsa court lagu hoga?"]),
    "due_date": (r"of each month", ["On which date is the EMI debited every month?", "Har mahine EMI kis tarikh ko kategi?"]),
    "agreement_no": (r"agreement no", ["What is my loan agreement number?", "Mera agreement number kya hai?"]),
    "kfs_part": (r"forms part of this agreement", ["Is the Key Fact Statement part of the agreement?",
                                                    "Kya KFS agreement ka hissa hai?"]),
}
UNANSWERABLE = ["Can I get a credit card with this loan?", "What is the lender's customer care number?",
                "Is there a gold loan option?", "Do you offer a home loan top-up?", "What is my CIBIL score?",
                "Kya mujhe cashback milega?", "Branch ka address kya hai?", "Is there a referral bonus?",
                "Kya loan pe free gift milega?", "Can I change my EMI date to the 20th?",
                "What documents do I need for KYC?", "Agent ka naam kya hai?"]


def build_questions(ag_index: int, clauses: list[segment.Clause]) -> list[tuple[str, str, set[int]]]:
    qs = []
    for k, (topic, (pat, variants)) in enumerate(TOPICS.items()):
        gold = {c.ord for c in clauses if re.search(pat, c.text, re.IGNORECASE | re.MULTILINE)}
        if gold:
            qs.append((topic, variants[(ag_index + k) % 2], gold))
    return qs[:15]


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
    parser, llm, emb = parse.DoclingParser(), llm_from_settings(), emb_from_settings()
    uid = uuid.uuid4()
    docs: list[tuple[uuid.UUID, list[tuple[str, str, set[uuid.UUID]]]]] = []
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO users (id, email, password_hash) VALUES (:u, 'e6@example.com', 'x')"), {"u": uid})
    for i in range(10):
        ag = f"AG{i + 1:02d}"
        pdf = ROOT / "datasets" / "agreements" / ag / "agreement.pdf"
        d = parser.parse(pdf, ocr=False, force_full_page_ocr=False)
        parse.confidence_pass(d, set())
        clauses = segment.segment(d.items)
        lex = terms.lexicon_terms(clauses)
        llm_terms, _ = await terms.llm_terms(llm, clauses, {t.clause_ord for t in lex})
        chunks = [(c, piece) for c in clauses for piece in retrieve.chunk_texts(c.text)]
        vecs = await emb.embed([((c.section_path + "\n") if c.section_path else "") + p for c, p in chunks], kind="passage")
        sid, did = uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO sessions (id, owner_id, mode, state, retention_until) VALUES (:s, :u, 'upload', 'ready', now() + interval '1 day')"),
                               {"s": sid, "u": uid})
            await conn.execute(text("INSERT INTO documents (id, session_id, kind, source, version, content_sha256, status) "
                                    "VALUES (:d, :s, 'loan_agreement', 'pdf', 1, :h, 'ready')"), {"d": did, "s": sid, "h": ag})
            ids = await ingest._write_clauses(SimpleNamespace(conn=conn), did, 1, clauses, lex + llm_terms)  # type: ignore[arg-type]
            for (c, piece), v in zip(chunks, vecs, strict=True):
                await conn.execute(text(
                    "INSERT INTO chunks (id, document_id, version, clause_id, page, section_path, text, embedding, "
                    "embedding_model) VALUES (:id, :d, 1, :c, :p, :sp, :t, CAST(:e AS vector), :m)"),
                    {"id": uuid.uuid4(), "d": did, "c": ids[c.ord], "p": c.page_start, "sp": c.section_path, "t": piece,
                     "e": "[" + ",".join(f"{x:.6f}" for x in v) + "]", "m": emb.name})
        qs = [(topic, q, {ids[o] for o in gold}) for topic, q, gold in build_questions(i, clauses)]
        docs.append((sid, qs))
        print(f"{ag}: {len(clauses)} clauses, {len(chunks)} chunks, {len(qs)} questions", flush=True)

    results: dict[str, list[tuple[str, str, int | None, float]]] = {"bm25": [], "vector": [], "hybrid": []}
    unans: list[float] = []
    async with engine.connect() as conn:
        for k, (sid, qs) in enumerate(docs):
            for mode in results:
                for topic, q, gold in qs:
                    top = await retrieve.retrieve(conn, emb, session_id=sid, user_id=uid, query=q, mode=mode,
                                                  use_threshold=False)
                    rank = next((r for r, c in enumerate(top, start=1) if c.clause_id in gold), None)
                    sim = max((c.similarity for c in top), default=0.0)
                    results[mode].append((topic, q, rank, sim))
            for q in UNANSWERABLE[k % 4 * 3:k % 4 * 3 + 3]:
                top = await retrieve.retrieve(conn, emb, session_id=sid, user_id=uid, query=q, use_threshold=False)
                unans.append(max((c.similarity for c in top), default=0.0))
    await engine.dispose()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")

    def metrics(rows: list) -> tuple[float, float]:
        return (sum(1 for r in rows if r[2]) / len(rows), sum(1 / r[2] for r in rows if r[2]) / len(rows))

    hyb = results["hybrid"]
    ans_sims = sorted(r[3] for r in hyb)
    best = int(ans_sims[0] * 100) / 100  # floor: never drops an answerable question's results
    rejected = sum(1 for s in unans if best is not None and s < best) / len(unans)

    today = dt.date.today().isoformat()
    L = [f"# E6 — Retrieval: BM25 vs vector vs hybrid ({today})", "",
         "> **Synthetic agreements and templated questions.** 10 generated agreements (digital PDFs), 150 templated "
         "questions (English and Hinglish alternating) with gold clauses chosen by topic patterns. Real documents and "
         "real user questions will be harder; these figures are optimistic and are not the HC-4 acceptance measurement.",
         "", "Pipeline: Docling → `segment.py` → lexicon + Gemma doc_terms → `intfloat/multilingual-e5-small` (384-dim, "
         "ADR-005 working choice) → Postgres `simple` FTS + pgvector HNSW → RRF k=60, +0.02 boost for matching "
         "table-row/doc_terms clauses on money queries (doc 09 §4).", "",
         "| Arm | Recall@5 | MRR | English R@5 | Hinglish R@5 |", "|---|---|---|---|---|"]
    for mode, rows in results.items():
        r5, mrr = metrics(rows)
        en = [r for r in rows if any(r[1] == TOPICS[r[0]][1][0] for _ in [0])]
        hi = [r for r in rows if r not in en]
        L.append(f"| {mode} | {'**' if mode == 'hybrid' else ''}{r5:.3f}{'**' if mode == 'hybrid' else ''} | {mrr:.3f} | "
                 f"{metrics(en)[0]:.3f} ({len(en)}) | {metrics(hi)[0]:.3f} ({len(hi)}) |")
    L += ["", "Target (doc 09 §8 / doc 12 A.3): hybrid Recall@5 ≥ 0.90.", "", "## T_ret calibration (cosine similarity of the best top-5 chunk)", "",
          f"- Answerable questions: median best similarity {ans_sims[len(ans_sims) // 2]:.3f}, min {ans_sims[0]:.3f}.",
          f"- Unanswerable questions ({len(unans)}): median {sorted(unans)[len(unans) // 2]:.3f}, max {max(unans):.3f}.",
          f"- Proposed **T_ret = {best}** (floor of the lowest answerable similarity, so no answerable question loses its results); it rejects "
          f"{100 * rejected:.0f}% of the unanswerable ones at retrieval. E5 similarities are compressed, so retrieval "
          "alone cannot separate unanswerable questions; NOT_IN_DOCUMENT for Ask (T3.4) also relies on the LLM + "
          "citation validator. Decision at HC-4.", "", "## Misses (hybrid)", "", "| Topic | Question | Rank |", "|---|---|---|"]
    L += [f"| {t} | {q} | — |" for t, q, rank, _ in hyb if rank is None] or ["| — | none | — |"]
    by_topic: dict[str, list] = {}
    for r in hyb:
        by_topic.setdefault(r[0], []).append(r)
    L += ["", "## Hybrid Recall@5 by topic", "", "| Topic | n | R@5 |", "|---|---|---|"]
    L += [f"| {t} | {len(rs)} | {metrics(rs)[0]:.2f} |" for t, rs in by_topic.items()]
    out = ROOT / "reports" / f"{today}_E6_retrieval.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[8:13]), f"\nT_ret={best} rejects {rejected:.2f}", out, sep="\n")


if __name__ == "__main__":
    asyncio.run(main())
