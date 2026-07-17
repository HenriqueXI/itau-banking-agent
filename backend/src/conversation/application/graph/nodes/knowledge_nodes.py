"""knowledge_flow: `retrieve` → `generate_answer` | `refuse_no_kb` (langgraph.md §1).

The floor decision belongs to the knowledge module (rag.md §3) — this flow only
routes on it. Generation sees evidence and nothing else: no history, no identity,
no weights-based recall. That's the hallucination control that actually holds.
"""

from decimal import Decimal

import structlog

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.ports.banking_workflow import HybridInvoiceGuidanceView
from conversation.application.ports.llm import LlmError, LlmMessage, MessageRole
from conversation.application.ports.retrieval import RetrievalError
from conversation.application.prompts import library
from conversation.application.responses import KNOWLEDGE_UNAVAILABLE, REFUSE_NO_KB, format_brl
from conversation.domain.history import last_user_message
from conversation.domain.values import ResourceSubject, Retrieval
from shared.application.ports.tracer import annotate

logger = structlog.get_logger(__name__)


def render_evidence(retrieval: Retrieval) -> str:
    """Delimited evidence blocks: chunks are data, never instructions (rag.md §7).

    The citation marker travels *with* the text so the model copies it verbatim
    instead of composing one from memory.
    """
    blocks = []
    for evidence in retrieval.evidence:
        blocks.append(
            f'<evidencia fonte="{evidence.citation.marker()}">\n{evidence.text}\n</evidencia>'
        )
    return "\n\n".join(blocks)


def make_retrieve(deps: GraphDependencies) -> GraphNode:
    async def retrieve(state: AgentState) -> AgentState:
        understanding = state.get("understanding")
        query = (understanding.params.get("query") if understanding else None) or state.get(
            "input_text", ""
        )
        try:
            retrieval = await deps.retrieval.retrieve(str(query))
        except RetrievalError:
            # Never answer from weights when the KB is down (rag.md §7).
            logger.warning("graph.retrieve.unavailable", thread_id=state["thread_id"])
            annotate(available=False)
            return {
                "response": KNOWLEDGE_UNAVAILABLE,
                "route": "knowledge_unavailable",
                "retrieval": None,
            }

        logger.info(
            "graph.retrieve",
            thread_id=state["thread_id"],
            below_floor=retrieval.below_floor,
            evidence=len(retrieval.evidence),
            best_score=retrieval.best_score,
        )
        # telemetry.md §1: the retrieve span carries query, k, scores and doc ids
        # — enough to answer "why did it refuse?" from the trace alone.
        annotate(
            query=str(query),
            k=len(retrieval.evidence),
            below_floor=retrieval.below_floor,
            best_score=retrieval.best_score,
            scores=[e.score for e in retrieval.evidence],
            document_ids=[e.citation.document_id for e in retrieval.evidence],
        )
        return {"retrieval": retrieval}

    return retrieve


def make_generate_answer(deps: GraphDependencies) -> GraphNode:
    async def generate_answer(state: AgentState) -> AgentState:
        retrieval = state.get("retrieval")
        if retrieval is None or not retrieval.evidence:  # defensive: routing guarantees evidence
            return {"response": REFUSE_NO_KB, "route": "refuse_no_kb"}

        question = last_user_message(state.get("messages", [])) or state.get("input_text", "")
        prompt = deps.prompts.render(
            library.GENERATE_ANSWER,
            evidence=render_evidence(retrieval),
            question=question,
        )
        try:
            completion = await deps.llm.complete(
                [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)],
                temperature=deps.config.generation_temperature,
                max_tokens=deps.config.answer_max_tokens,
            )
        except LlmError as error:
            logger.warning("graph.generate_answer.llm_failed", error=str(error))
            raise  # the fallback edge owns provider exhaustion (langgraph.md §6)

        return {
            "response": completion.text.strip(),
            "route": "generate_answer",
            "provider": completion.provider,
        }

    return generate_answer


def make_generate_hybrid(_: GraphDependencies) -> GraphNode:
    """Combine typed current data with a cited, document-backed policy reference."""

    async def generate_hybrid(state: AgentState) -> AgentState:
        retrieval = state.get("retrieval")
        result = state.get("result")
        if (
            retrieval is None
            or not retrieval.evidence
            or not isinstance(result, HybridInvoiceGuidanceView)
        ):
            return {"response": REFUSE_NO_KB, "route": "refuse_no_kb"}
        invoice = result.invoice
        debits = sorted(
            (
                (description, amount)
                for description, amount in result.statement.entries
                if amount < 0
            ),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:3]
        amounts: tuple[Decimal, ...] = (invoice.amount, *(amount for _, amount in debits))
        subject = state.get("resource_subject")
        subject_name = (
            subject.name
            if isinstance(subject, ResourceSubject) and subject.name
            else "o cliente informado"
        )
        account_reference = (
            "sua conta"
            if not isinstance(subject, ResourceSubject) or subject.is_self
            else f"conta de {subject_name}"
        )
        text = (
            f"Dado atual da {account_reference}: a fatura do cartao final {invoice.last4} esta em "
            f"{format_brl(invoice.amount)} e vence no dia {invoice.due_date}."
        )
        if debits:
            highlights = "; ".join(
                f"{description}: {format_brl(amount)}" for description, amount in debits
            )
            text += f" As movimentacoes que mais pesaram foram: {highlights}."
        text += (
            " Para evitar juros, siga as condicoes de pagamento e vencimento da politica "
            f"aplicavel: {retrieval.citations[0].marker()}."
        )
        return {
            "response": text,
            "route": "generate_hybrid",
            "narration_amounts": amounts,
        }

    return generate_hybrid


def make_refuse_no_kb(deps: GraphDependencies) -> GraphNode:
    async def refuse_no_kb(state: AgentState) -> AgentState:
        """Template, not generation (FR-2.3): the one moment we must not be
        creative is when we have nothing to say."""
        retrieval = state.get("retrieval")
        logger.info(
            "graph.refuse_no_kb",
            thread_id=state["thread_id"],
            best_score=retrieval.best_score if retrieval else None,
        )
        return {"response": REFUSE_NO_KB, "route": "refuse_no_kb"}

    return refuse_no_kb
