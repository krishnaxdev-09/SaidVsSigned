# Local equivalents of CI (doc 01 §11, A18). `make ci` runs exactly what .github/workflows/ci.yml runs.
COMPOSE = docker compose -f infra/docker-compose.yml --env-file .env

.PHONY: env up db down migrate seed ci ci-backend ci-frontend

env:
	sh scripts/gen-env.sh

up:
	$(COMPOSE) up -d --build

db:
	$(COMPOSE) up -d db

down:
	$(COMPOSE) down

migrate:
	cd backend && set -a && . ../.env && set +a && uv run alembic upgrade head && uv run python -m app.cli seed-consent-texts

ci: ci-backend ci-frontend

ci-backend:
	cd backend && uv sync --frozen && uv run ruff check . && uv run mypy app && uv run pytest -q -p no:warnings

ci-frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run lint && npm run typecheck && npm test && npm run build
