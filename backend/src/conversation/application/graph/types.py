"""Shared node typing for the graph layer.

Its own module so the node factories and the builder can agree on the shape
without importing each other.
"""

from collections.abc import Awaitable, Callable

from conversation.application.graph.state import AgentState

type GraphNode = Callable[[AgentState], Awaitable[AgentState]]
