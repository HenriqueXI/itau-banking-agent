# backend/

Python 3.12 modular monolith: `shared/` kernel (Settings, DI container, logging/masking, tracing with `TracerPort` + Langfuse/no-op adapters, `trace_id` contextvar), Alembic migrations, `/health`, `identity_access` (JWT login, auth middleware, step-up challenge service, `AuthorizationService`), `knowledge` (RAG: ingestion + retrieval; Chroma/Gemini/Ollama adapters), `conversation` (the LangGraph agent, guardrails, prompts, LLM adapters + fallback chain) exposed at `POST /api/agui` (SSE, AG-UI), `banking` (card limits, PIX, eligibility, MCP client) and `audit` (immutable trail via domain events).

## Layout

```
backend/
├── pyproject.toml              # uv-managed; ruff, mypy, pytest config
├── src/
│   ├── shared/                 # kernel: Result type, domain event base, DI container, config,
│   │                           #   logging/masking, telemetry (tracer port + correlation ids)
│   ├── identity_access/        # auth (JWT), RBAC, step-up auth
│   ├── conversation/           # chat sessions, LangGraph graph + checkpoints
│   ├── knowledge/              # RAG: ingestion, retrieval, citation
│   ├── banking/                # cards, PIX, eligibility, MCP client adapter
│   ├── audit/                  # audit trail (event consumer)
│   └── api/                    # FastAPI app: routers, AG-UI endpoint, middleware
├── mcp_server/                 # standalone MCP server exposing banking tools
└── tests/                      # mirrors src/ (unit, integration per module)
```

Each module follows the hexagonal skeleton:

```
<module>/
├── domain/          # entities, value objects, domain events, domain services — NO framework imports
├── application/     # use cases, ports (Protocol classes)
└── adapters/        # inbound (routers) + outbound (postgres, chroma, llm, mcp) implementations
```

## Rules (enforced by import-linter in CI)

1. `domain/` imports only stdlib + `shared/domain`.
2. `application/` imports `domain/` + `shared/`, never `adapters/`.
3. Modules communicate via application-layer interfaces or domain events — never by importing another module's internals.
4. Only `api/` and the DI container may wire adapters to ports.
