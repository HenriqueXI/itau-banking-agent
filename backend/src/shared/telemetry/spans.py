"""The span-name registry — stable identifiers, not free text (telemetry.md §5).

Dashboards, saved Langfuse filters and the demo checklist key off these strings.
A refactor may rename a function freely; renaming a span silently breaks every
consumer that never imported our code. So the names live here, once, and later
PRDs reference the constant instead of retyping the string (PRD-013 Technical
Notes).

Node spans are named after the graph node they wrap (`builder._traced`), which
is why the constants below must keep matching the node names in
`conversation.application.graph.builder` — `tests/unit/conversation/
test_graph_tracing.py` fails if they drift.
"""

from typing import Final

# The turn-level trace (one per user message); the session is the thread_id.
TURN: Final = "turn"

# Node spans (telemetry.md §1 hierarchy).
INPUT_GUARDRAILS: Final = "input_guardrails"
UNDERSTAND: Final = "understand"
RETRIEVE: Final = "retrieve"
AUTHORIZE: Final = "authorize"
RESOLVE_CUSTOMER_REFERENCE: Final = "resolve_customer_reference"
VALIDATE_RULES: Final = "validate_rules"
GENERATE_ANSWER: Final = "generate_answer"
NARRATE: Final = "narrate"
OUTPUT_GUARDRAILS: Final = "output_guardrails"

#: Node spans telemetry.md documents by name. A node here that vanishes from the
#: graph is a contract change, not a refactor — hence the test, not a comment.
DOCUMENTED_NODE_SPANS: Final = frozenset(
    {
        INPUT_GUARDRAILS,
        UNDERSTAND,
        RETRIEVE,
        RESOLVE_CUSTOMER_REFERENCE,
        AUTHORIZE,
        GENERATE_ANSWER,
        OUTPUT_GUARDRAILS,
    }
)

#: Spans for nodes PRD-007/008 add. Not asserted against the graph yet — they
#: exist so those PRDs reference a constant rather than inventing a spelling.
PLANNED_NODE_SPANS: Final = frozenset({VALIDATE_RULES, NARRATE})

TOOL_PREFIX: Final = "tool:"


def tool_span(tool_name: str) -> str:
    """`tool:create_pix` — the MCP call span name (telemetry.md §1)."""
    return f"{TOOL_PREFIX}{tool_name}"
