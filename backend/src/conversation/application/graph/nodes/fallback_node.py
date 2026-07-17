"""`fallback`: the edge every node has (langgraph.md §6).

An exception is a bug or an infra failure — never a user-facing mystery and never
a fabricated answer. The user gets an apology plus a correlation id; the trace
gets the exception; the state stays at the last good checkpoint.
"""

import structlog

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.responses import fallback_error

logger = structlog.get_logger(__name__)


def make_fallback(deps: GraphDependencies) -> GraphNode:
    async def fallback(state: AgentState) -> AgentState:
        correlation_id = state.get("correlation_id") or str(deps.id_generator.new_id())
        logger.error(
            "graph.fallback",
            thread_id=state.get("thread_id"),
            correlation_id=correlation_id,
            route=state.get("route"),
        )
        return {
            "response": fallback_error(correlation_id),
            "route": "fallback",
            "correlation_id": correlation_id,
        }

    return fallback
