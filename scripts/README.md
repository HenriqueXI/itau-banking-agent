# scripts/

Developer and operational scripts.

| Script | Purpose |
|---|---|
| `seed.py` | Create demo users (customer/manager/admin), accounts, cards with known values used by tests |
| `smoke.py` | Assert `GET /health == 200` on the composed stack |
| `ingest_kb.py` | Load KB documents (tariff PDFs, FAQ, regulations) into ChromaDB via the knowledge module's ingestion pipeline |
| `eval_rag.py` | Run the RAG golden-dataset evaluation (retrieval hit rate, citations, faithfulness) |
| `eval_agent.py` | Run the agent behavior/adversarial evaluation (intent routing, reference resolution, behavioral contract) |

## Running them from the host

`.env` carries **compose hostnames** (`ollama`, `chromadb`, `postgres`) because the app runs inside the stack. A script run from the host resolves none of those — override the one you need on the command line:

```bash
cd backend
# agent evals against a local Ollama (host install, not the compose profile)
LLM_PROVIDER=ollama OLLAMA_URL=http://localhost:11434   uv run --env-file ../.env python ../scripts/eval_agent.py --suite 2
```

`make eval-agent` / `make eval-rag` assume the stack is up and reachable under the compose names.

## Rules

1. Scripts call application-layer use cases — never raw SQL/vector-store writes that bypass domain rules.
2. Idempotent: safe to re-run (`seed` upserts, `ingest` skips unchanged documents by content hash).
