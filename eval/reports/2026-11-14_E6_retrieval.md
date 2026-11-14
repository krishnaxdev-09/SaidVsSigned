# E6 — Retrieval: BM25 vs vector vs hybrid (2026-11-14)

> **Synthetic agreements and templated questions.** 10 generated agreements (digital PDFs), 150 templated questions (English and Hinglish alternating) with gold clauses chosen by topic patterns. Real documents and real user questions will be harder; these figures are optimistic and are not the HC-4 acceptance measurement.

Pipeline: Docling → `segment.py` → lexicon + Gemma doc_terms → `intfloat/multilingual-e5-small` (384-dim, ADR-005 working choice) → Postgres `simple` FTS + pgvector HNSW → RRF k=60, +0.02 boost for matching table-row/doc_terms clauses on money queries (doc 09 §4).

| Arm | Recall@5 | MRR | English R@5 | Hinglish R@5 |
|---|---|---|---|---|
| bm25 | 0.947 | 0.816 | 0.959 (73) | 0.935 (77) |
| vector | 0.880 | 0.689 | 0.973 (73) | 0.792 (77) |
| hybrid | **0.993** | 0.841 | 1.000 (73) | 0.987 (77) |

Target (doc 09 §8 / doc 12 A.3): hybrid Recall@5 ≥ 0.90.

## T_ret calibration (cosine similarity of the best top-5 chunk)

- Answerable questions: median best similarity 0.829, min 0.733.
- Unanswerable questions (30): median 0.820, max 0.847.
- Proposed **T_ret = 0.73** (floor of the lowest answerable similarity, so no answerable question loses its results); it rejects 0% of the unanswerable ones at retrieval. E5 similarities are compressed, so retrieval alone cannot separate unanswerable questions; NOT_IN_DOCUMENT for Ask (T3.4) also relies on the LLM + citation validator. Decision at HC-4.

## Misses (hybrid)

| Topic | Question | Rank |
|---|---|---|
| prepayment | Loan jaldi band karne pe charge lagega? | — |

## Hybrid Recall@5 by topic

| Topic | n | R@5 |
|---|---|---|
| processing_fee | 10 | 1.00 |
| file_charge | 10 | 1.00 |
| interest | 10 | 1.00 |
| rate_basis | 10 | 1.00 |
| emi | 10 | 1.00 |
| tenure | 10 | 1.00 |
| prepayment | 10 | 0.90 |
| late | 10 | 1.00 |
| net | 10 | 1.00 |
| total | 10 | 1.00 |
| amount | 10 | 1.00 |
| bounce | 2 | 1.00 |
| repayment | 10 | 1.00 |
| law | 10 | 1.00 |
| due_date | 10 | 1.00 |
| documentation | 2 | 1.00 |
| insurance | 2 | 1.00 |
| agreement_no | 4 | 1.00 |
