# SaidSigned

Upload-first loan "said vs signed" verification assistant. Architecture and plan: `docs/` (start with
`docs/revision/MVP_IMPLEMENTATION_PLAN.md`). **Status: Phase 0 (foundation) implemented, in review.** No STT, OCR, RAG,
finance or verification features exist yet.

## Run locally
```sh
make env          # writes .env with random local secrets (never commit it)
make db           # Postgres 16 + pgvector
make migrate      # alembic upgrade + seed DRAFT consent texts (legal review HC-7 pending)
cd backend && uv run uvicorn app.main:create_app --factory --reload   # API on :8000
cd frontend && npm install && npm run dev                             # UI on :5173 (proxies /api and /ws)
make ci           # same checks as .github/workflows/ci.yml (needs `make db`)
```
`make up` starts the full stack, but **MinIO images currently cannot be pulled** (see Q-16 in
`docs/14_ARCHITECTURE_DECISIONS_AND_OPEN_QUESTIONS.md`), so `/readyz` reports `storage: error` until that is resolved.
