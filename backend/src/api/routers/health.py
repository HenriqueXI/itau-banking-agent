"""GET /health — component statuses. DB ping now; Chroma/MCP join in later PRDs."""

import asyncio
from typing import Any
from urllib.request import urlopen

import structlog
from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from shared.adapters.outbox import PostgresOutboxRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


async def _chroma_heartbeat(url: str) -> None:
    """Probe Chroma without creating a collection as a health side effect."""
    await asyncio.to_thread(urlopen, f"{url.rstrip('/')}/api/v2/heartbeat", timeout=2)


def _provider_configured(request: Request) -> bool:
    settings = request.app.state.container.settings
    configured = {
        "gemini": bool(settings.gemini_api_key),
        "openrouter": bool(settings.openrouter_api_key),
        "ollama": bool(settings.ollama_url),
    }
    return any(
        configured.get(name.strip(), False) for name in settings.llm_fallback_order.split(",")
    )


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, Any]:
    components: dict[str, str] = {}

    try:
        async with request.app.state.container.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception:
        logger.warning("health.database_unreachable")
        components["database"] = "unreachable"

    if request.app.state.container.settings.env != "test":
        try:
            await _chroma_heartbeat(request.app.state.container.settings.chroma_url)
            components["chroma"] = "ok"
        except Exception:
            logger.warning("health.chroma_unreachable")
            components["chroma"] = "unreachable"

        components["provider"] = "configured" if _provider_configured(request) else "unconfigured"

        try:
            await request.app.state.banking.client.ping()
            components["mcp"] = "ok"
        except Exception:
            logger.warning("health.mcp_unreachable")
            components["mcp"] = "unreachable"

        try:
            container = request.app.state.container
            async with container.session_factory() as session:
                stats = await PostgresOutboxRepository(session).stats(now=container.clock.now())
            components["outbox"] = "degraded" if stats.failed_count else "ok"
            components["outbox_pending"] = str(stats.pending_count)
            components["outbox_dead_letters"] = str(stats.failed_count)
            components["outbox_oldest_pending_at"] = (
                stats.oldest_pending_at.isoformat() if stats.oldest_pending_at else ""
            )
            components["outbox_lag_seconds"] = (
                str(max(0.0, (container.clock.now() - stats.oldest_pending_at).total_seconds()))
                if stats.oldest_pending_at
                else "0"
            )
        except Exception:
            logger.warning("health.outbox_unreachable")
            components["outbox"] = "unreachable"

    healthy = all(
        status == "ok"
        for name, status in components.items()
        if name in {"database", "chroma", "outbox", "mcp"}
    )
    response.status_code = 200 if healthy else 503
    return {"status": "ok" if healthy else "degraded", "components": components}
