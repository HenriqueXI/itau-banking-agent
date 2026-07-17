# infra/

Local/dev infrastructure. Compose includes postgres, chromadb, Langfuse, MCP,
backend and the frontend; the dev overlay enables backend reload and
`next dev` for the frontend.

## Layout

```
infra/
├── docker-compose.yml          # postgres, chromadb, langfuse (+db), backend, mcp-server, frontend
├── docker-compose.dev.yml      # dev overrides: hot reload and exposed ports
├── backend.Dockerfile          # also used by the mcp-server service (same image, different command)
└── frontend.Dockerfile
```

## Rules

1. One `docker compose up` boots everything a demo needs.
2. All configuration via environment variables (`.env.example` at repo root is the contract; never commit `.env`).
3. Ollama is optional and runs natively on the host; the backend reaches it through `host.docker.internal` when selected in `.env`.
