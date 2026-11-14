# SaidSigned

SaidSigned checks a recorded loan-sales call against the loan agreement the customer was given, and flags anything the
agent promised that the signed terms contradict, citing the exact quote, timestamp and clause.

## Status (2026-11-14)
**14 of 40 MVP tasks done (35%).** Stage 0 (foundation) is complete. Stage 1 is in progress: recording upload works;
speech-to-text, speaker confirmation and claim extraction are next.
Stage-by-stage status: `AGENTS.md` · evidence per task: `docs/revision/MVP_TASK_STATUS.md` ·
plan: `docs/revision/MVP_IMPLEMENTATION_PLAN.md`.

| Works now | Not built yet |
|---|---|
| Register/login, sessions, consent + recording attestation, revocation | Speech-to-text (ElevenLabs, T1.2) |
| Recording upload (WAV/MP3/M4A/AAC/WebM/OGG), validation, normalisation, playback snippets | Claim extraction (LLM, T1.6) |
| Job queue, live status events, audit log | Agreement upload/OCR and the said-vs-signed comparison (Stage 2) |

## Requirements
Docker, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20, and `ffmpeg`.

## Configure
```sh
make env          # writes .env with random local secrets (never commit it)
```
Then add the provider keys to `.env` (see `.env.example`):

| Setting | Used for | Value |
|---|---|---|
| `ELEVENLABS_API_KEY` | Speech-to-text (Scribe v2) | your ElevenLabs key |
| `MANTLE_API_KEY` | LLM via Amazon Bedrock Mantle | your Bedrock API key |
| `MANTLE_BASE_URL` | Mantle endpoint (Gemma 4 needs `/openai/v1`) | `https://bedrock-mantle.us-east-1.api.aws/openai/v1` |
| `MANTLE_MODEL` | LLM model | `google.gemma-4-31b` |

## Run
```sh
make up           # full stack: Postgres, MinIO, API on :8000, worker
cd frontend && npm install && npm run dev   # UI on http://localhost:5173
```
For backend development without Docker for the API:
```sh
make db && make migrate
cd backend && uv run uvicorn app.main:create_app --factory --reload
```

## Test
```sh
make ci           # what CI runs: lint, types, backend tests, frontend tests + build, dataset check
```
`make ci` needs Postgres running (`make db`) and `ffmpeg`; it fails rather than skipping tests if either is missing.

## Notes
- MinIO is built from pinned upstream source because the official images can no longer be pulled (Q-16 in
  `docs/14_ARCHITECTURE_DECISIONS_AND_OPEN_QUESTIONS.md`).
- Use only synthetic agreements (`eval/datasets/agreements`) and team-recorded calls: real customer data needs the legal
  checkpoint HC-7 first. Consent texts are drafts pending that review.
