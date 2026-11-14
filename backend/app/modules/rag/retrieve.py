"""Chunking and hybrid retrieval — doc 09 §3–§4 (T2.4).

Indexing: one chunk per clause (table rows already carry their header row; section path stored), split only if a
clause exceeds ~500 tokens. Retrieval:
1. Deterministic synonym expansion (foreclosure ↔ prepayment ↔ part-payment ↔ "jaldi band"; processing fee ↔ login
   fee ↔ file charge; EMI ↔ kist; …) for the full-text side.
2. FTS top 20 (`simple` config, OR of the expanded terms) + vector top 20, **restricted to the current version of
   this session's documents that are `ready`, and to the session owner** (enforced in the SQL, not by callers).
3. RRF (k = 60) with a +0.02 boost for table-row and doc_terms clauses on money queries (only when the clause also
   matches the full-text query).
4. Top 5. If the best cosine similarity among them is below `T_ret` (doc 08 §5, calibrated in E6) → empty.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.llm.base import EmbeddingProvider

RRF_K = 60
TOP_N = 20
TOP_K = 5
MONEY_BOOST = 0.02
MAX_CHUNK_CHARS = 2000  # ≈ 500 tokens

SYNONYMS: list[set[str]] = [
    {"foreclosure", "foreclose", "prepayment", "prepay", "pre-payment", "part-payment", "part payment",
     "jaldi band", "early closure", "close early"},
    {"processing fee", "processing charge", "login fee", "file charge", "file charges", "processing"},
    {"emi", "kist", "instalment", "installment", "monthly payment"},
    {"interest", "rate of interest", "roi", "byaj", "interest rate"},
    {"penal", "penalty", "late payment", "late fee", "delay", "overdue", "late charge"},
    {"insurance", "credit-shield", "credit shield", "cover", "bima"},
    {"tenure", "term", "months", "duration", "kitne mahine"},
    {"loan amount", "principal", "sanctioned", "sanction"},
    {"disbursement", "disbursed", "in hand", "net amount", "haath mein"},
    {"bounce", "dishonour", "ecs", "nach return", "cheque bounce"},
    {"gst", "tax"},
]
MONEY_QUERY = re.compile(
    r"fee|charge|rate|interest|emi|kist|amount|cost|price|₹|rs\.?|rupee|%|percent|gst|insurance|penal|kitna|kitni|"
    r"how much|tenure|disburs|payable|byaj", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+|[ऀ-ॿ]+")
STOPWORDS = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "what", "how", "does", "do",
             "my", "me", "i", "it", "there", "any", "will", "be", "this", "that", "with", "kya", "hai", "hain", "ka",
             "ki", "ke", "ko", "se", "mein", "kitna", "kitni", "batao", "much", "can", "if", "at"}


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    clause_id: uuid.UUID
    document_id: uuid.UUID
    document_version: int
    page: int
    text: str
    bm25_rank: int | None
    vec_rank: int | None
    rrf: float
    similarity: float


def t_ret(path: Path | None = None) -> float | None:
    from app.core.config import get_settings

    v = yaml.safe_load((path or get_settings().THRESHOLDS_FILE).read_text()).get("T_ret")
    return None if v is None else float(v)


def chunk_texts(clause_text: str) -> list[str]:
    """Clause-aligned chunks; a clause longer than ~500 tokens is split at sentence ends."""
    if len(clause_text) <= MAX_CHUNK_CHARS:
        return [clause_text]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.;])\s+", clause_text):
        if cur and len(cur) + len(sent) + 1 > MAX_CHUNK_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    return out + ([cur] if cur else [])


def expand(query: str) -> list[str]:
    """Query words plus synonyms of any phrase group the query mentions (deterministic)."""
    q = query.lower()
    terms = {w for w in _WORD.findall(q) if w not in STOPWORDS}
    for group in SYNONYMS:
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", q) for p in group):
            for p in group:
                terms.update(w for w in _WORD.findall(p) if w not in STOPWORDS)
    return sorted(terms)


def is_money_query(query: str) -> bool:
    return bool(MONEY_QUERY.search(query))


_SCOPE = ("FROM chunks c JOIN documents d ON d.id = c.document_id AND d.version = c.version "
          "JOIN sessions s ON s.id = d.session_id "
          "JOIN clauses cl ON cl.id = c.clause_id "
          "WHERE d.session_id = :sid AND s.owner_id = :uid AND s.deleted_at IS NULL AND d.deleted_at IS NULL "
          "AND d.status = 'ready'")


async def retrieve(conn: AsyncConnection, embedder: EmbeddingProvider, *, session_id: uuid.UUID,
                   user_id: uuid.UUID, query: str, k: int = TOP_K, threshold: float | None = None,
                   use_threshold: bool = True, mode: str = "hybrid") -> list[RetrievedChunk]:
    """mode: hybrid | bm25 | vector (the single-arm modes exist for the E6 comparison)."""
    (qv,) = await embedder.embed([query], kind="query")
    qvec = "[" + ",".join(f"{x:.6f}" for x in qv) + "]"
    params = {"sid": session_id, "uid": user_id, "q": qvec, "n": TOP_N}
    cols = ("c.id, c.clause_id, c.document_id, c.version, c.page, c.text, cl.is_table_row, "
            "EXISTS (SELECT 1 FROM doc_terms t WHERE t.clause_id = c.clause_id) AS has_terms, "
            "1 - (c.embedding <=> CAST(:q AS vector)) AS sim")
    bm25: Sequence[Any] = []
    terms = expand(query)
    if terms and mode in ("hybrid", "bm25"):
        tsq = " | ".join(terms)
        bm25 = (await conn.execute(text(
            f"SELECT {cols}, ts_rank_cd(c.tsv, to_tsquery('simple', :tsq)) AS r {_SCOPE} "
            "AND c.tsv @@ to_tsquery('simple', :tsq) ORDER BY r DESC, c.id LIMIT :n"), {**params, "tsq": tsq})).all()
    vec: Sequence[Any] = []
    if mode in ("hybrid", "vector"):
        vec = (await conn.execute(text(
            f"SELECT {cols} {_SCOPE} ORDER BY c.embedding <=> CAST(:q AS vector), c.id LIMIT :n"), params)).all()
    money = is_money_query(query)
    rows: dict[uuid.UUID, dict] = {}
    for arm, res in (("bm25", bm25), ("vec", vec)):
        for rank, r in enumerate(res, start=1):
            e = rows.setdefault(r.id, {"row": r, "bm25": None, "vec": None, "rrf": 0.0})
            e[arm] = rank
            e["rrf"] += 1.0 / (RRF_K + rank)
    for e in rows.values():
        # Boost only clauses that also match lexically: +0.02 exceeds a whole RRF rank (1/61), so boosting every
        # table row would let unrelated KFS rows outrank the matching clause (found in T2.4 tests).
        if money and e["bm25"] is not None and (e["row"].is_table_row or e["row"].has_terms):
            e["rrf"] += MONEY_BOOST
    ranked = sorted(rows.values(), key=lambda e: (-e["rrf"], str(e["row"].id)))[:k]
    out = [RetrievedChunk(chunk_id=e["row"].id, clause_id=e["row"].clause_id, document_id=e["row"].document_id,
                          document_version=e["row"].version, page=e["row"].page, text=e["row"].text,
                          bm25_rank=e["bm25"], vec_rank=e["vec"], rrf=e["rrf"], similarity=float(e["row"].sim))
           for e in ranked]
    thr = threshold if threshold is not None else (t_ret() if use_threshold else None)
    if use_threshold and thr is not None and (not out or max(c.similarity for c in out) < thr):
        return []
    return out
