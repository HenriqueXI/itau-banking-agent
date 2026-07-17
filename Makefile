# Local parity with CI. On Windows use WSL or Git Bash.

COMPOSE      := docker compose --env-file .env -f infra/docker-compose.yml
COMPOSE_DEV  := $(COMPOSE) -f infra/docker-compose.dev.yml
BACKEND      := cd backend &&

.PHONY: up dev down logs nuke seed smoke ingest-kb eval-rag eval-rag-offline eval-agent eval-agent-ci eval-agent-dry check check-all lint type test test-integration build

# ---- stack -------------------------------------------------------------
up:
	$(COMPOSE) up -d --build

dev:
	$(COMPOSE_DEV) up --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

nuke:
	@echo "This removes ALL volumes (postgres data included)."
	$(COMPOSE) down -v

seed:
	$(BACKEND) uv run --env-file ../.env python ../scripts/seed.py

smoke:
	python scripts/smoke.py

# ---- knowledge base ------------------------------------------------------
# Idempotent: unchanged documents are skipped. Needs the stack up + an
# embedding provider configured (EMBEDDING_PROVIDER + its key).
ingest-kb:
	$(BACKEND) uv run --env-file ../.env python ../scripts/ingest_kb.py

# Gated eval against the configured provider. Run ingest-kb first.
eval-rag:
	$(BACKEND) uv run --env-file ../.env python ../scripts/eval_rag.py --live \
		--report ../eval-report.md

# Diagnostic only: no network, lexical embedder — exercises the pipeline, does
# NOT certify retrieval quality. Never a substitute for eval-rag.
eval-rag-offline:
	$(BACKEND) uv run python ../scripts/eval_rag.py

# ---- agent evals -----------------------------------------------------------
# Gated run against the configured provider: intent routing, reference
# resolution, behavioral contract. Costs quota — run before releases and after
# any prompt or provider change.
eval-agent:
	$(BACKEND) uv run --env-file ../.env python ../scripts/eval_agent.py 		--report ../eval-agent-report.md

# Deterministic subset against a local Ollama (no quota, no judge). Needs
# `ollama serve` + `ollama pull llama3.1:8b` on the host.
eval-agent-ci:
	$(BACKEND) LLM_PROVIDER=ollama OLLAMA_URL=http://localhost:11434 		uv run --env-file ../.env python ../scripts/eval_agent.py --mode ci 		--report ../eval-agent-report.md

# Dataset validation only: no model, no quota, gates nothing.
eval-agent-dry:
	$(BACKEND) uv run python ../scripts/eval_agent.py --dry-run

# ---- quality (CI stages 1-3 = check; +4 = check-all) ---------------------
lint:
	$(BACKEND) uv run ruff check src tests migrations
	$(BACKEND) uv run ruff format --check src tests migrations
	$(BACKEND) uv run lint-imports

type:
	$(BACKEND) uv run mypy

test:
	$(BACKEND) uv run pytest -q

test-integration:
	$(BACKEND) uv run pytest -q -m integration

check: lint type test

check-all: check test-integration

build:
	$(COMPOSE) build
