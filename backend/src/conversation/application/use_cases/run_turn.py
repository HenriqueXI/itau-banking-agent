"""RunTurn: own the thread, run the graph, stream what the guardrails approved.

Two rules shape this use case:

1. **Ownership before state.** The thread↔user binding is checked (and claimed
   on first use) before the graph reads a checkpoint, so a user can never touch
   another user's conversation — not even to learn it exists (PRD006-FR-6).
2. **Nothing streams before the output ring.** Node lifecycle events (retrieval)
   stream live, but the answer text streams only after `output_guardrails`
   approved it (ADR-013). A blocked answer that already reached the client is a
   leak with a nice progress bar.

It is also where a turn's `trace_id` is born (FR-7.2): generated here, bound to
the log context and the trace, put into graph state, and stamped onto every
domain event the turn raises — one id, so an audit row and a Langfuse trace are
two views of one thing.
"""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import structlog

from conversation.application.dto import (
    AgentEvent,
    CitationsEmitted,
    ConfirmationRequired,
    ResumeTurnCommand,
    RunError,
    RunFinished,
    RunStarted,
    RunTurnCommand,
    StateSnapshot,
    StepUpRequired,
    TextDelta,
    ToolCallEnded,
    ToolCallStarted,
)
from conversation.application.ports.banking_workflow import (
    BankingWorkflowPort,
    LimitAuthorizationDeniedView,
    LimitConfirmationView,
    LimitReceiptView,
    OperationFailedView,
    PixConfirmationView,
    PixReceiptView,
    PixStepUpView,
)
from conversation.application.ports.thread_repository import ThreadRepositoryPort
from conversation.application.responses import (
    NO_PENDING_OPERATION,
    OUTPUT_BLOCKED,
    denied,
    fallback_error,
    format_brl,
)
from conversation.domain.errors import thread_not_owned
from conversation.domain.events import ConversationTurnCompleted
from conversation.domain.output_guardrails import OutputVerdict, inspect_output
from conversation.domain.values import ResourceSubject, Retrieval
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.application.ports.tracer import Scope, TracerPort, TraceSpec
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result
from shared.logging.setup import correlation_context
from shared.telemetry import spans
from shared.telemetry.correlation import new_trace_id

logger = structlog.get_logger(__name__)

# Streamed in chunks so the UI renders progressively; the text is already final,
# so the chunk size is a rendering choice, not a protocol constraint.
_CHUNK_CHARS = 24


def _telemetry_output(response: str, subject: object) -> str:
    """Do not export an authorized third-party display name to a trace.

    The user-facing response can identify a customer after RBAC permits it, but
    observability exports remain subject to the PII policy.
    """
    if isinstance(subject, ResourceSubject) and not subject.is_self and subject.name:
        return response.replace(subject.name, "[cliente autorizado]")
    return response


class RunTurn:
    def __init__(
        self,
        *,
        graph: Any,
        threads: ThreadRepositoryPort,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
        tracer: TracerPort,
        banking: BankingWorkflowPort | None = None,
    ) -> None:
        self._graph = graph
        self._threads = threads
        self._events = events
        self._clock = clock
        self._ids = id_generator
        self._tracer = tracer
        self._banking = banking

    async def authorize_thread(self, command: RunTurnCommand) -> Result[None, DomainError]:
        """Claim-or-verify the thread binding. Split out from `stream` so the
        transport can answer 403 *before* opening an SSE stream."""
        existing = await self._threads.get(command.thread_id)
        if existing is None:
            await self._threads.claim(command.thread_id, command.user_id)
            return Ok(None)
        if not existing.belongs_to(command.user_id):
            logger.warning(
                "conversation.thread_ownership_rejected",
                thread_id=command.thread_id,
                user_id=str(command.user_id),
            )
            return Err(thread_not_owned())
        return Ok(None)

    async def stream_resume(self, command: ResumeTurnCommand) -> AsyncIterator[AgentEvent]:
        """Answer an interrupt response (ag-ui.md).

        PRD-006 ships the topology without gates, so no run is ever paused and
        every resume lands here as "no longer active" — the same answer an
        expired confirmation gets (BR-6.2). PRD-007 adds the pending-op
        validation chain behind this same entry point; the client contract does
        not change when it does.
        """
        trace_id = new_trace_id()
        with correlation_context(trace_id):
            run_id = str(self._ids.new_id())
            yield RunStarted(thread_id=command.thread_id, run_id=run_id)

            pending = await self._pending_interrupt(command.thread_id)
            logger.info(
                "conversation.resume",
                thread_id=command.thread_id,
                operation_hash=command.operation_hash,
                pending=pending,
            )

            if self._banking is None:
                response = NO_PENDING_OPERATION
                route = "no_pending_operation"
            elif command.stage == "step_up" and command.challenge_id is not None:
                step_up_result = await self._banking.resolve_step_up(
                    user=command.user,
                    user_id=command.user_id,
                    operation_hash=command.operation_hash,
                    challenge_id=command.challenge_id,
                    code=command.response,
                )
                if isinstance(step_up_result, PixConfirmationView):
                    yield ConfirmationRequired(
                        operation_hash=step_up_result.operation_hash,
                        operation="fazer_pix",
                        current_amount="",
                        requested_amount=str(step_up_result.amount),
                        expires_at=step_up_result.expires_at,
                        issued_at=self._clock.now().isoformat(),
                        recipient_key_masked=step_up_result.recipient_key_masked,
                        account_id=step_up_result.account_id,
                    )
                    response, route = (
                        "Codigo confirmado. Revise e confirme o PIX exibido.",
                        "await_confirmation",
                    )
                else:
                    response, route = NO_PENDING_OPERATION, "no_pending_operation"
            else:
                result = await self._banking.resolve_confirmation(
                    user=command.user,
                    user_id=command.user_id,
                    operation_hash=command.operation_hash,
                    response=command.response,
                )
                if isinstance(result, LimitAuthorizationDeniedView):
                    response, route = (
                        denied(
                            result.reason,
                            action="update_card_limit",
                            own_resource=True,
                        ),
                        "denied_response",
                    )
                elif isinstance(result, LimitConfirmationView):
                    yield ConfirmationRequired(
                        operation_hash=result.operation_hash,
                        operation="alterar_limite",
                        current_amount=str(result.current_limit),
                        requested_amount=str(result.requested_limit),
                        expires_at=result.expires_at,
                        issued_at=self._clock.now().isoformat(),
                    )
                    response = (
                        "Sua resposta ficou ambigua. Confirme ou cancele a alteracao exibida."
                    )
                    route = "await_confirmation"
                elif isinstance(result, LimitReceiptView):
                    response = (
                        f"Limite do cartao final {result.last4} atualizado de "
                        f"{format_brl(result.old_limit)} "
                        f"para {format_brl(result.new_limit)}."
                    )
                    response, route = self._guard_narration(
                        response, amounts=(result.old_limit, result.new_limit)
                    )
                elif isinstance(result, PixConfirmationView):
                    yield ConfirmationRequired(
                        operation_hash=result.operation_hash,
                        operation="fazer_pix",
                        current_amount="",
                        requested_amount=str(result.amount),
                        expires_at=result.expires_at,
                        issued_at=self._clock.now().isoformat(),
                        recipient_key_masked=result.recipient_key_masked,
                        account_id=result.account_id,
                    )
                    response, route = (
                        "Sua resposta ficou ambigua. Confirme ou cancele o PIX exibido.",
                        "await_confirmation",
                    )
                elif isinstance(result, PixReceiptView):
                    response = (
                        f"PIX de {format_brl(result.amount)} para "
                        f"{result.recipient_key_masked} realizado. "
                        f"Comprovante {result.e2e_id}."
                    )
                    response, route = self._guard_narration(response, amounts=(result.amount,))
                elif isinstance(result, OperationFailedView):
                    # Honest failure (workflows.md edge case): FAILED + events
                    # are already persisted; never dress this up as success or
                    # a generic error.
                    if result.tool == "fazer_pix":
                        response = (
                            "Nao foi possivel concluir o PIX agora. "
                            "A transferencia nao foi realizada. Tente novamente em instantes."
                        )
                    else:
                        response = (
                            "Nao foi possivel concluir a alteracao de limite agora. "
                            "Seu limite nao foi alterado. Tente novamente em instantes."
                        )
                    route = "failed"
                else:
                    response = NO_PENDING_OPERATION
                    route = "no_pending_operation"
            message_id = str(self._ids.new_id())
            for chunk in _chunks(response):
                yield TextDelta(message_id=message_id, delta=chunk)
            yield StateSnapshot(
                route=route,
                intent=None,
                pending_operation_hash=None,
                data_changed=route == "narrate"
                and isinstance(result, (LimitReceiptView, PixReceiptView)),
            )
            yield RunFinished(thread_id=command.thread_id, run_id=run_id, route=route)

    def _guard_narration(self, response: str, *, amounts: tuple[Decimal, ...]) -> tuple[str, str]:
        """O5 on the resume path (PRD007-FR-7): the one leg that narrates money
        receipts outside the graph's output ring runs the same inspection."""
        inspection = inspect_output(response, expected_amounts=amounts)
        if inspection.verdict is OutputVerdict.BLOCK:
            logger.warning(
                "guardrails.output.blocked_on_resume",
                checks=[f.check_id for f in inspection.flags],
            )
            return OUTPUT_BLOCKED, "output_blocked"
        return inspection.text, "narrate"

    async def _pending_interrupt(self, thread_id: str) -> bool:
        """Is this thread paused at an interrupt? Only a checkpointed graph can
        answer; a stateless one (evals, unit tests) has no pending anything."""
        if getattr(self._graph, "checkpointer", None) is None:
            return False
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": thread_id}})
        return bool(getattr(snapshot, "next", ()))

    async def stream(self, command: RunTurnCommand) -> AsyncIterator[AgentEvent]:
        trace_id = new_trace_id()
        # Bound before anything else runs: from here on every log line, span and
        # domain event of this turn carries the id, without being handed it.
        with correlation_context(trace_id):
            spec = TraceSpec(
                name=spans.TURN,
                trace_id=trace_id,
                # Session = conversation, so Langfuse groups turns into the
                # thread they belong to (ADR-010).
                session_id=command.thread_id,
                user_id=str(command.user_id),
                input=command.message,
            )
            with self._tracer.trace(spec) as scope:
                async for event in self._stream_traced(command, trace_id, scope):
                    yield event

    async def _stream_traced(
        self, command: RunTurnCommand, trace_id: str, scope: Scope
    ) -> AsyncIterator[AgentEvent]:
        run_id = str(self._ids.new_id())
        correlation_id = str(self._ids.new_id())
        yield RunStarted(thread_id=command.thread_id, run_id=run_id)

        config = {"configurable": {"thread_id": command.thread_id}}
        initial = {
            "thread_id": command.thread_id,
            # Identity is injected per invocation from the verified JWT and is
            # never read back from the checkpoint (langgraph.md §3).
            "user": command.user,
            "user_id": command.user_id,
            "input_text": command.message,
            "ui_context": command.ui_context,
            # Turn-local values must never survive through a LangGraph
            # checkpoint. History and server-owned pending state are retained
            # separately, but a previous monetary narration must not make O5
            # inspect the next response against stale values.
            "resolved_resource": None,
            "resource_subject": None,
            "understanding": None,
            "retrieval": None,
            "response": "",
            "route": "",
            "narration_amounts": (),
            "guardrail_flags": [],
            "regenerated": False,
            "correlation_id": correlation_id,
            "trace_id": trace_id,
            "confirmation": None,
            "step_up": None,
            "result": None,
        }

        final_state: dict[str, Any] = {}
        retrieval_call_id: str | None = None
        try:
            async for step in self._graph.astream(initial, config, stream_mode="updates"):
                # updates mode yields {node_name: state_patch} per super-step.
                for node_name, update in (step or {}).items():
                    final_state.update(update or {})
                    if node_name != "retrieve":
                        continue
                    retrieval_call_id = str(self._ids.new_id())
                    yield ToolCallStarted(
                        tool_call_id=retrieval_call_id,
                        tool_name="buscar_conhecimento",
                        args={"query": final_state.get("input_text", "")},
                    )
                    yield ToolCallEnded(
                        tool_call_id=retrieval_call_id,
                        tool_name="buscar_conhecimento",
                        result_summary=_retrieval_summary((update or {}).get("retrieval")),
                    )
        except Exception as error:
            logger.exception("conversation.run_failed", thread_id=command.thread_id)
            scope.update(
                level="ERROR",
                status_message=f"{type(error).__name__}: {error}",
                metadata={"correlation_id": correlation_id},
            )
            yield RunError(message=fallback_error(correlation_id), correlation_id=correlation_id)
            return

        route = final_state.get("route", "unknown")
        response = final_state.get("response", "")
        retrieval = final_state.get("retrieval")
        confirmation = final_state.get("confirmation")

        if isinstance(confirmation, LimitConfirmationView):
            yield ConfirmationRequired(
                operation_hash=confirmation.operation_hash,
                operation="alterar_limite",
                current_amount=str(confirmation.current_limit),
                requested_amount=str(confirmation.requested_limit),
                expires_at=confirmation.expires_at,
                issued_at=self._clock.now().isoformat(),
            )
        elif isinstance(confirmation, PixConfirmationView):
            yield ConfirmationRequired(
                operation_hash=confirmation.operation_hash,
                operation="fazer_pix",
                current_amount="",
                requested_amount=str(confirmation.amount),
                expires_at=confirmation.expires_at,
                issued_at=self._clock.now().isoformat(),
                recipient_key_masked=confirmation.recipient_key_masked,
                account_id=confirmation.account_id,
            )
        step_up = final_state.get("step_up")
        if isinstance(step_up, PixStepUpView):
            yield StepUpRequired(
                operation_hash=step_up.operation_hash, expires_at=step_up.expires_at
            )

        message_id = str(self._ids.new_id())
        for chunk in _chunks(response):
            yield TextDelta(message_id=message_id, delta=chunk)

        if route in {"generate_answer", "generate_hybrid"} and isinstance(retrieval, Retrieval):
            yield CitationsEmitted(citations=retrieval.citations)

        understanding = final_state.get("understanding")
        pending = final_state.get("pending_operation")
        yield StateSnapshot(
            route=route,
            intent=understanding.intent.value if understanding else None,
            pending_operation_hash=pending.operation_hash if pending else None,
            data_changed=route in {"narrate", "failed"}
            and understanding is not None
            and understanding.intent.value in {"update_card_limit", "create_pix"},
        )

        # telemetry.md §1 trace metadata: the turn's verdict in one place, so a
        # dashboard can filter without opening the spans.
        scope.update(
            output=_telemetry_output(response, final_state.get("resource_subject")),
            metadata={
                "route": route,
                "intent": understanding.intent.value if understanding else None,
                "provider": final_state.get("provider"),
                "guardrail_flags": [f.check_id for f in final_state.get("guardrail_flags", [])],
                "pending_operation_hash": pending.operation_hash if pending else None,
                "correlation_id": correlation_id,
            },
        )

        await self._events.publish(
            ConversationTurnCompleted(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                actor_user_id=str(command.user_id),
                thread_id=command.thread_id,
                intent=understanding.intent.value if understanding else "unknown",
                route=route,
                citation_count=len(retrieval.citations) if isinstance(retrieval, Retrieval) else 0,
                provider=final_state.get("provider"),
            )
        )
        yield RunFinished(thread_id=command.thread_id, run_id=run_id, route=route)


def _retrieval_summary(retrieval: object) -> str:
    if not isinstance(retrieval, Retrieval):
        return "base de conhecimento indisponível"
    if retrieval.below_floor or not retrieval.evidence:
        return "nenhum trecho relevante encontrado"
    return f"{len(retrieval.evidence)} trecho(s) encontrado(s)"


def _chunks(text: str) -> list[str]:
    return [text[i : i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]
