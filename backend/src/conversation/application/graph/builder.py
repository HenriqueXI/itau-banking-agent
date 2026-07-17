"""Graph assembly — the topology in langgraph.md §1, in code.

Read this next to the diagram: the edges here ARE the security model. There is
exactly one path from a banking intent to any effect, and it goes through
`authorize`. The LLM chooses a *word*; these edges choose what happens.

Every node is wrapped by `_guarded`, which implements the fallback edge from
langgraph.md §6 (an exception routes to the apology template with the state
intact) — LangGraph has no per-node error edge, so the wrapper is the seam.

That same wrapper opens the node's span (PRD013-FR-3), which is why a new node
is traced the moment it is added: instrumentation is a property of the topology,
not a line a future PR remembers to write. It parents onto whatever scope the
runner opened (`RunTurn`), so a node that fails is an ERROR span rather than a
missing one — the fallback runs *inside* the span that recorded the failure.
"""

from contextlib import nullcontext
from typing import Any, cast

import structlog
from langgraph.graph import END, START, StateGraph

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.nodes.banking_nodes import (
    make_authorize,
    make_await_confirmation,
    make_await_step_up,
    make_cancelled,
    make_denied_response,
    make_execute,
    make_gate,
    make_narrate,
    make_resolve_customer_reference,
    make_resolve_resource,
    make_validate_rules,
)
from conversation.application.graph.nodes.conversational_nodes import make_clarify, make_smalltalk
from conversation.application.graph.nodes.fallback_node import make_fallback
from conversation.application.graph.nodes.guardrail_nodes import (
    make_blocked_response,
    make_input_guardrails,
    make_output_guardrails,
)
from conversation.application.graph.nodes.knowledge_nodes import (
    make_generate_answer,
    make_generate_hybrid,
    make_refuse_no_kb,
    make_retrieve,
)
from conversation.application.graph.nodes.understand_node import make_understand
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.domain.tools import tool_for_intent
from conversation.domain.values import Intent
from shared.application.ports.tracer import current_scope

logger = structlog.get_logger(__name__)

FALLBACK_ROUTE = "fallback"

#: State keys safe to attach to a node span's output. `response`, `input_text`
#: and `messages` are excluded: the tracer masks PII, but a span is not the
#: place to re-export conversation content that the trace already carries once.
_SPAN_OUTPUT_KEYS = ("route", "provider")


def _guarded(name: str, node: GraphNode, fallback: GraphNode) -> GraphNode:
    async def run(state: AgentState) -> AgentState:
        parent = current_scope()
        # No open scope ⇒ nobody is tracing this run (a direct graph call in a
        # unit test). A span without a trace has nowhere to go, so skip it
        # rather than invent a parent.
        opener = (
            parent.span(name, metadata={"thread_id": state.get("thread_id")})
            if parent is not None
            else nullcontext(None)
        )
        with opener as span:
            try:
                update = await node(state)
            except Exception as error:
                logger.exception("graph.node_failed", node=name, thread_id=state.get("thread_id"))
                if span is not None:
                    span.update(level="ERROR", status_message=f"{type(error).__name__}: {error}")
                return await fallback(state)
            if span is not None:
                span.update(output={key: update.get(key) for key in _SPAN_OUTPUT_KEYS})
            return update

    run.__name__ = name
    return run


def _route_after_input(state: AgentState) -> str:
    if state.get("route") == FALLBACK_ROUTE:
        return "output_guardrails"
    if any(flag.blocking for flag in state.get("guardrail_flags", [])):
        return "blocked_response"
    return "understand"


def _route_after_understand(state: AgentState) -> str:
    if state.get("route") == FALLBACK_ROUTE:
        return "output_guardrails"

    understanding = state.get("understanding")
    if understanding is None:
        return "clarify"
    if understanding.intent is Intent.SMALLTALK:
        return "smalltalk"
    # Required parameters are clarified before authorization. Resource
    # ambiguity is different: it must reach deterministic resolution so a
    # validated panel selection can win over an LLM's generic question.
    if understanding.missing_param is not None or understanding.intent is Intent.UNCLEAR:
        return "clarify"
    if understanding.ambiguity:
        spec = tool_for_intent(understanding.intent)
        if spec is None or spec.resource_kind not in {"card", "account"}:
            return "clarify"
    if understanding.intent is Intent.KB_QUERY:
        return "retrieve"
    if tool_for_intent(understanding.intent) is not None:
        return "resolve_customer_reference"
    return "clarify"


def _route_after_customer_reference(state: AgentState) -> str:
    if state.get("route") == FALLBACK_ROUTE:
        return "output_guardrails"
    if state.get("route") == "cancelled":
        return "cancelled"
    return "authorize"


def _route_after_retrieve(state: AgentState) -> str:
    if state.get("route") in (FALLBACK_ROUTE, "knowledge_unavailable"):
        return "output_guardrails"
    retrieval = state.get("retrieval")
    if retrieval is None or retrieval.below_floor or not retrieval.evidence:
        return "refuse_no_kb"
    understanding = state.get("understanding")
    if understanding is not None and understanding.intent is Intent.HYBRID_INVOICE_GUIDANCE:
        return "generate_hybrid"
    return "generate_answer"


def _route_after_authorize(state: AgentState) -> str:
    if state.get("route") == FALLBACK_ROUTE:
        return "output_guardrails"
    if state.get("route") == "denied_response":
        return "denied_response"
    return "resolve_resource"


def _route_after_resource_resolution(state: AgentState) -> str:
    if state.get("route") == FALLBACK_ROUTE:
        return "output_guardrails"
    if state.get("route") == "clarify":
        return "clarify"
    if state.get("route") == "cancelled":
        return "cancelled"
    return "validate_rules"


def _route_after_validate(state: AgentState) -> str:
    return "cancelled" if state.get("route") == "cancelled" else "gate"


def _route_after_gate(state: AgentState) -> str:
    if state.get("route") == "denied_response":
        return "denied_response"
    if state.get("route") == "cancelled":
        return "cancelled"
    return "execute"


def _route_after_execute(state: AgentState) -> str:
    if state.get("route") == "step_up_required":
        return "await_step_up"
    if state.get("route") == "confirmation_required":
        return "await_confirmation"
    if state.get("route") == "cancelled":
        return "cancelled"
    understanding = state.get("understanding")
    if understanding is not None and understanding.intent is Intent.HYBRID_INVOICE_GUIDANCE:
        return "retrieve"
    return "narrate"


def _route_after_output(state: AgentState) -> str:
    if state.get("route") == "regenerate":
        understanding = state.get("understanding")
        if understanding is not None and understanding.intent is Intent.HYBRID_INVOICE_GUIDANCE:
            return "generate_hybrid"
        return "generate_answer"
    return END


def build_graph(deps: GraphDependencies, *, checkpointer: Any | None = None) -> Any:
    """Compile the agent graph. `checkpointer=None` yields a stateless graph —
    useful for evals; the API always passes the Postgres saver (langgraph.md §4)."""
    fallback = make_fallback(deps)

    def add(builder: StateGraph[AgentState], name: str, node: GraphNode) -> None:
        # LangGraph types nodes against its own protocol zoo; the wrapper is a
        # plain async callable, which it accepts at runtime.
        builder.add_node(name, cast(Any, _guarded(name, node, fallback)))

    builder: StateGraph[AgentState] = StateGraph(AgentState)
    add(builder, "input_guardrails", make_input_guardrails(deps))
    add(builder, "blocked_response", make_blocked_response(deps))
    add(builder, "understand", make_understand(deps))
    add(builder, "clarify", make_clarify(deps))
    add(builder, "smalltalk", make_smalltalk(deps))
    add(builder, "retrieve", make_retrieve(deps))
    add(builder, "generate_answer", make_generate_answer(deps))
    add(builder, "generate_hybrid", make_generate_hybrid(deps))
    add(builder, "refuse_no_kb", make_refuse_no_kb(deps))
    add(builder, "resolve_customer_reference", make_resolve_customer_reference(deps))
    add(builder, "authorize", make_authorize(deps))
    add(builder, "resolve_resource", make_resolve_resource(deps))
    add(builder, "denied_response", make_denied_response(deps))
    add(builder, "validate_rules", make_validate_rules(deps))
    add(builder, "gate", make_gate(deps))
    add(builder, "execute", make_execute(deps))
    add(builder, "await_confirmation", make_await_confirmation(deps))
    add(builder, "await_step_up", make_await_step_up(deps))
    add(builder, "narrate", make_narrate(deps))
    add(builder, "cancelled", make_cancelled(deps))
    add(builder, "output_guardrails", make_output_guardrails(deps))

    builder.add_edge(START, "input_guardrails")
    builder.add_conditional_edges(
        "input_guardrails",
        _route_after_input,
        ["blocked_response", "understand", "output_guardrails"],
    )
    builder.add_conditional_edges(
        "understand",
        _route_after_understand,
        [
            "clarify",
            "smalltalk",
            "retrieve",
            "resolve_customer_reference",
            "output_guardrails",
        ],
    )
    builder.add_conditional_edges(
        "resolve_customer_reference",
        _route_after_customer_reference,
        ["authorize", "cancelled", "output_guardrails"],
    )
    builder.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        ["generate_answer", "generate_hybrid", "refuse_no_kb", "output_guardrails"],
    )
    builder.add_conditional_edges(
        "authorize",
        _route_after_authorize,
        ["denied_response", "resolve_resource", "output_guardrails"],
    )
    builder.add_conditional_edges(
        "resolve_resource",
        _route_after_resource_resolution,
        ["clarify", "validate_rules", "cancelled", "output_guardrails"],
    )
    builder.add_conditional_edges("validate_rules", _route_after_validate, ["gate", "cancelled"])
    builder.add_conditional_edges(
        "gate", _route_after_gate, ["execute", "cancelled", "denied_response"]
    )
    builder.add_conditional_edges(
        "execute",
        _route_after_execute,
        ["await_step_up", "await_confirmation", "retrieve", "narrate", "cancelled"],
    )

    for terminal in (
        "blocked_response",
        "clarify",
        "smalltalk",
        "generate_answer",
        "generate_hybrid",
        "refuse_no_kb",
        "denied_response",
        "await_confirmation",
        "await_step_up",
        "narrate",
        "cancelled",
    ):
        builder.add_edge(terminal, "output_guardrails")

    builder.add_conditional_edges(
        "output_guardrails", _route_after_output, ["generate_answer", END]
    )

    return builder.compile(checkpointer=checkpointer)
