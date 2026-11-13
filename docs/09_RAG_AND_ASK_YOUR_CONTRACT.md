# 09 — Retrieval & "Ask Your Contract"

> **Version 0.2.1 — 2026-11-13 (upload-first MVP revision; HC-5 note in 0.2.1).** Original: `_archive/v0.1-2026-11-13/09_…md`.
> **Scope:**
> - [MVP] hybrid retrieval (BM25 + vector + RRF), used by the contract engine.
> - [MVP-limited] basic Ask Your Contract with validated citations and **numeric questions routed to the finance engine**.
> - [Deferred → Later-L4] cross-encoder reranking, LLM query translation, conversational follow-ups.

## 1. Purpose
Retrieve the right clauses for verification. Answer customer questions **only** from the uploaded documents, with citations that are checked mechanically.

- **Depends on:** doc 05 (clauses/chunks), doc 06 (numeric route), `EmbeddingProvider` (ADR-005 open), doc 11.

## 2. Interfaces
```python
class EmbeddingProvider(Protocol):
    name: str; dim: int
    async def embed(self, texts: list[str], *, kind: Literal["query","passage"]) -> list[list[float]]: ...

class RetrievedChunk(BaseModel):
    chunk_id: UUID; clause_id: UUID; document_id: UUID; document_version: int; page: int; text: str
    bm25_rank: int | None; vec_rank: int | None; rrf: float
```

## 3. Indexing [MVP]
- Chunks are clause-aligned (≤ 500 tokens, with section path and table header prepended).
- `tsv` uses the `simple` config.
- `embedding vector(EMBEDDING_DIM)`: **the dimension is a migration parameter** set by ADR-005 (v0.1 hard-coded 1024).
- Each chunk stores its `embedding_model`.

## 4. Retrieval [MVP]
1. Deterministic synonym expansion, e.g. foreclosure ↔ prepayment ↔ part-payment ↔ "jaldi band"; processing fee ↔ login fee ↔ file charge; EMI ↔ kist.
2. FTS top 20 + vector top 20, **restricted to the current versions of this session's documents and to the owner** (repository layer; tested).
3. RRF (k = 60), with a +0.02 boost for table-row and `doc_terms` clauses on money queries.
4. Top 5. If the top score is below `T_ret` (doc 08 §5) → empty.

[Deferred → Later-L4] cross-encoder rerank and LLM query translation (if E6 shows Recall@5 < 0.9 for Hinglish queries, this moves up — decision at HC-4).

## 5. Ask Your Contract [MVP-limited]
**Routing** (`rag/router.py`):
1. A deterministic intent check first: regex for calculation intent ("total", "kitna padega", "how much will I pay", "EMI for", "after fees", "in hand").
2. If ambiguous, an LLM structured classifier `{route: quote | finance | out_of_scope}`.

| Route | Handling |
|---|---|
| `quote` (e.g. "what is the processing fee?", "is there a foreclosure charge?") | Retrieval → LLM answer with citations → **citation validator** |
| `finance` (any computed number) | Build `LoanInputs` from **document terms only** via `SourcedFactory` (doc 06 §3) → MVP formulas only → templated answer citing the calculation trace and the source clauses. Before HC-5 approval, **every** finance-route answer is "This calculation isn't available yet". After HC-5, MVP formulas only. APR, prepayment and penal stay unavailable for the whole MVP (doc 06 §4a) |
| `out_of_scope` (legal advice, lender recommendations) | Fixed polite refusal + "Ask the lender" |

**Citation validator** (quote route):
1. Every cited chunk was in the provided context.
2. Each quote is a verbatim substring.
3. Every sentence has a citation.
4. Every number in the answer appears in a cited quote. **The LLM may not compute**; calculations only go through the finance route.

On failure: 1 corrective retry (`schema` class, doc 01 §6). After that: "I couldn't find a reliable answer in your document" plus the top 3 clauses as links.

`NOT_IN_DOCUMENT` → "Your documents don't appear to say this. Ask the lender to put it in writing."

**Answer schema:** `{answer_text, citations[{chunk_id, quote}], not_in_document, route, calculation_id?}`.

## 6. API / events
- `POST /api/v1/sessions/{id}/qa {question}` (Idempotency-Key) → `202 {qa_id}`
- Event `qa.answer{qa_id, status: routing|retrieving|answering|done|failed, answer?}`
- `GET /api/v1/sessions/{id}/qa`
- Suggested questions come from UNCLEAR/NOT_VERIFIED results plus a fixed list.

## 7. Testing
- Retrieval: 150 questions with gold clauses. Recall@5, MRR. BM25 vs vector vs hybrid (E6).
- Answers: 60 questions incl. 15 unanswerable; faithfulness graded by humans.
- Routing: 50 labelled questions; numeric questions must route to finance (≥ 95%).
- Validator: 100% rejection of answers containing numbers not present in quotes.
- Tenant isolation.

## 8. Acceptance criteria
- Hybrid Recall@5 ≥ 0.90 (measured).
- ≥ 90% of unanswerable questions return `NOT_IN_DOCUMENT`.
- 0 answers with invalid citations.
- 0 LLM-computed numbers delivered.

## 9. Human review
- ADR-005 at HC-4.
- 20 graded answers at the Phase 3 review.

## 10. Common failures
- English stemming dropping Hinglish tokens.
- Letting the LLM "help" with arithmetic.
- Retrieving from superseded document versions.
