"""`input_guardrails`, `blocked_response`, `output_guardrails` (guardrails.md).

Rings 2 and 4. Both fail closed: a check that raises is treated as a block, not
as a pass, because a guardrail that errors open is decoration.
"""

import structlog

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.nodes.knowledge_nodes import render_evidence
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.json_repair import parse_json_object
from conversation.application.ports.llm import LlmError, LlmMessage, MessageRole
from conversation.application.prompts import library
from conversation.application.responses import (
    BLOCKED_INPUT,
    OUTPUT_BLOCKED,
    REFUSE_NO_KB,
    SECRETS_WARNING,
)
from conversation.domain.events import GuardrailTriggered
from conversation.domain.input_guardrails import inspect_input
from conversation.domain.output_guardrails import OutputVerdict, inspect_output
from conversation.domain.values import Disposition, GuardrailFlag, Role, Turn
from shared.application.ports.tracer import annotate

logger = structlog.get_logger(__name__)

_GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grounded"],
}

ROUTES_REQUIRING_CITATIONS = frozenset({"generate_answer", "generate_hybrid"})
ROUTES_REQUIRING_AMOUNT_INTEGRITY = frozenset({"narrate", "generate_hybrid"})
# Template-produced text: deterministic copy we wrote, so the judge has nothing
# to add and an unavailable judge must not block it (llm-providers.md §3).
TEMPLATE_ROUTES = frozenset(
    {
        "blocked_response",
        "refuse_no_kb",
        "denied_response",
        "await_confirmation",
        "narrate",
        "cancelled",
        "fallback",
    }
)


async def _publish(
    deps: GraphDependencies, state: AgentState, flag: GuardrailFlag, ring: str
) -> None:
    await deps.events.publish(
        GuardrailTriggered(
            event_id=deps.id_generator.new_id(),
            occurred_at=deps.clock.now(),
            actor_user_id=str(state["user_id"]),
            thread_id=state["thread_id"],
            check_id=flag.check_id,
            disposition=flag.disposition.value,
            ring=ring,
        )
    )


def make_input_guardrails(deps: GraphDependencies) -> GraphNode:
    async def input_guardrails(state: AgentState) -> AgentState:
        raw = state.get("input_text", "")
        try:
            inspection = inspect_input(raw, max_chars=deps.config.max_input_chars)
        except Exception:  # fail closed (guardrails.md §4)
            logger.exception("guardrails.input.error")
            flag = GuardrailFlag(
                check_id="I0", disposition=Disposition.BLOCK, detail="guardrail component error"
            )
            return {"guardrail_flags": [flag], "input_text": ""}

        for flag in inspection.flags:
            if flag.disposition in (Disposition.BLOCK, Disposition.SANITIZE):
                await _publish(deps, state, flag, ring="input")

        messages = [*state.get("messages", []), Turn(role=Role.USER, content=inspection.text)]
        logger.info(
            "guardrails.input",
            thread_id=state["thread_id"],
            blocked=inspection.blocked,
            checks=[f.check_id for f in inspection.flags],
        )
        # Check ids and dispositions, never the triggering text: the span says
        # which ring fired, the trace's input already holds the message once.
        annotate(
            blocked=inspection.blocked,
            checks=[f.check_id for f in inspection.flags],
            dispositions=[f.disposition.value for f in inspection.flags],
        )
        return {
            "input_text": inspection.text,
            "messages": messages,
            "guardrail_flags": list(inspection.flags),
        }

    return input_guardrails


def make_blocked_response(deps: GraphDependencies) -> GraphNode:
    async def blocked_response(state: AgentState) -> AgentState:
        """One generic refusal for every block reason — the response must not
        tell an attacker which pattern fired (guardrails.md §4)."""
        return {"response": BLOCKED_INPUT, "route": "blocked_response"}

    return blocked_response


def make_output_guardrails(deps: GraphDependencies) -> GraphNode:
    async def output_guardrails(state: AgentState) -> AgentState:
        route = state.get("route", "")
        retrieval = state.get("retrieval")
        citations = retrieval.citations if retrieval else ()
        requires_citations = route in ROUTES_REQUIRING_CITATIONS
        expected_amounts = (
            tuple(state.get("narration_amounts", ()))
            if route in ROUTES_REQUIRING_AMOUNT_INTEGRITY
            else ()
        )

        inspection = inspect_output(
            state.get("response", ""),
            citations=citations,
            requires_citations=requires_citations,
            expected_amounts=expected_amounts,
        )
        for flag in inspection.flags:
            await _publish(deps, state, flag, ring="output")

        annotate(
            verdict=inspection.verdict.value,
            checks=[f.check_id for f in inspection.flags],
            requires_citations=requires_citations,
            citation_count=len(citations),
        )

        if inspection.verdict is OutputVerdict.BLOCK:
            logger.warning(
                "guardrails.output.blocked",
                thread_id=state["thread_id"],
                checks=[f.check_id for f in inspection.flags],
            )
            return _final(state, OUTPUT_BLOCKED, route="output_blocked")

        text = inspection.text
        already_retried = state.get("regenerated", False)

        if inspection.verdict is OutputVerdict.REGENERATE:
            # O1: regenerate once → refusal (guardrails.md §2). The retry is a
            # graph edge back to generate_answer; a second failure means the
            # model won't cite, and an unverifiable claim is worse than a no.
            logger.warning(
                "guardrails.output.uncited",
                thread_id=state["thread_id"],
                retried=already_retried,
            )
            if already_retried:
                return _final(state, REFUSE_NO_KB, route="refuse_no_kb")
            return {"route": "regenerate", "regenerated": True}

        # Hybrid replies contain typed MCP facts that are intentionally absent
        # from the KB evidence. Their citation is still mandatory, but judging
        # those facts against the document would reject correct account data.
        if (
            requires_citations
            and route != "generate_hybrid"
            and deps.config.grounding_judge_enabled
        ):
            grounded = await _judge_grounding(deps, state, text)
            if not grounded:
                logger.warning(
                    "guardrails.output.ungrounded",
                    thread_id=state["thread_id"],
                    retried=already_retried,
                )
                if already_retried:
                    return _final(state, REFUSE_NO_KB, route="refuse_no_kb")
                return {"route": "regenerate", "regenerated": True}

        if any(f.check_id == "I5" for f in state.get("guardrail_flags", [])):
            text = f"{SECRETS_WARNING}\n\n{text}"

        return _final(state, text, route=route)

    return output_guardrails


def _final(state: AgentState, text: str, *, route: str) -> AgentState:
    retrieval = state.get("retrieval")
    citations = (
        retrieval.citations if route in ROUTES_REQUIRING_CITATIONS and retrieval is not None else ()
    )
    messages = [
        *state.get("messages", []),
        Turn(role=Role.ASSISTANT, content=text, citations=citations),
    ]
    return {"response": text, "route": route, "messages": messages}


async def _judge_grounding(deps: GraphDependencies, state: AgentState, answer: str) -> bool:
    """O2. Assist only: the deterministic checks already ran, and the judge can
    only *tighten* the outcome. Judge unavailable on a grounding-required answer
    ⇒ fail closed (llm-providers.md §3)."""
    retrieval = state.get("retrieval")
    if retrieval is None or not retrieval.evidence:
        return False

    prompt = deps.prompts.render(
        library.GROUNDING_JUDGE,
        evidence=render_evidence(retrieval),
        answer=answer,
    )
    try:
        completion = await deps.llm.complete(
            [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)],
            json_schema=_GROUNDING_SCHEMA,
            temperature=0.0,
            max_tokens=256,
        )
    except LlmError:
        logger.warning("guardrails.output.judge_unavailable", thread_id=state["thread_id"])
        return False

    payload = parse_json_object(completion.text)
    if payload is None or not isinstance(payload.get("grounded"), bool):
        logger.warning("guardrails.output.judge_unparseable", thread_id=state["thread_id"])
        return False
    return bool(payload["grounded"])
