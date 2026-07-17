# Multi-stage: uv install layer (locked deps, cached) -> slim runtime, non-root.
# Build context = repo root (see docker-compose.yml).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY scripts/seed.py ./scripts/seed.py
# The local launcher runs KB ingestion inside the backend container after the
# stack is healthy. Keep the reproducible demo corpus with the script.
COPY scripts/ingest_kb.py ./scripts/ingest_kb.py
COPY backend/tests/fixtures/kb ./backend/tests/fixtures/kb
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER appuser
EXPOSE 8000

# Dev entrypoint contract (PRD001-FR-4): migrations before serving.
CMD ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port 8000"]
