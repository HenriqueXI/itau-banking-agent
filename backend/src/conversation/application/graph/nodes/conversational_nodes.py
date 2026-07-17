"""`clarify` and `smalltalk` (langgraph.md §2).

Both are cheap generations with tight prompts. `clarify` exists so the agent
asks instead of guessing (FR-1.4) — its single most important property is that
it asks about ONE thing, which is why the gap is computed in code (tool schema)
and only phrased by the model.
"""

import structlog

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.ports.llm import LlmMessage, MessageRole
from conversation.application.prompts import library
from conversation.domain.history import last_user_message

logger = structlog.get_logger(__name__)

_PARAM_GAPS = {
    "amount": "o valor da operação",
    "pix_key": "a chave PIX do destinatário",
    "card_id": "qual cartão",
    "account_id": "qual conta",
    "query": "sobre qual assunto é a dúvida",
}


def _gap_description(state: AgentState) -> str:
    understanding = state.get("understanding")
    if understanding is None:
        return "não ficou claro o que o usuário quer"
    if understanding.missing_param:
        return f"falta {_PARAM_GAPS.get(understanding.missing_param, understanding.missing_param)}"
    if understanding.ambiguity:
        return understanding.ambiguity
    return "não ficou claro o que o usuário quer"


def make_clarify(deps: GraphDependencies) -> GraphNode:
    async def clarify(state: AgentState) -> AgentState:
        response = state.get("clarification_response")
        if response:
            return {"response": response, "route": "clarify"}
        gap = _gap_description(state)
        prompt = deps.prompts.render(
            library.CLARIFY,
            request=last_user_message(state.get("messages", [])) or state.get("input_text", ""),
            gap=gap,
        )
        completion = await deps.llm.complete(
            [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)],
            temperature=deps.config.generation_temperature,
            max_tokens=128,
        )
        logger.info("graph.clarify", thread_id=state["thread_id"], gap=gap)
        return {
            "response": completion.text.strip(),
            "route": "clarify",
            "provider": completion.provider,
        }

    return clarify


def make_smalltalk(deps: GraphDependencies) -> GraphNode:
    async def smalltalk(state: AgentState) -> AgentState:
        prompt = deps.prompts.render(library.SMALLTALK, message=state.get("input_text", ""))
        completion = await deps.llm.complete(
            [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)],
            temperature=deps.config.generation_temperature,
            max_tokens=128,
        )
        logger.info("graph.smalltalk", thread_id=state["thread_id"])
        return {
            "response": completion.text.strip(),
            "route": "smalltalk",
            "provider": completion.provider,
        }

    return smalltalk
