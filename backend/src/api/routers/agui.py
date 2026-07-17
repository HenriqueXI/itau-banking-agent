"""POST /api/agui — the AG-UI endpoint (api.md, ag-ui.md).

SSE out, JWT in. Two things happen before a single byte streams: the rate limit
and the thread-ownership check. Both must answer with an HTTP status rather than
an in-stream error, because a client that got 200 + a stream has already been
told "this thread is yours".

Event names are the AG-UI wire vocabulary; the application layer speaks its own
(`conversation.application.dto`), and this module is the only translation.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.dependencies import CurrentUser
from api.problem import ProblemError
from conversation.adapters.outbound.postgres.thread_repository import PostgresThreadRepository
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
from conversation.application.use_cases.run_turn import RunTurn
from conversation.domain.values import Role
from shared.adapters.event_publisher import event_transaction
from shared.domain.result import Err

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")


class AguiMessage(BaseModel):
    role: str
    content: str


class ResumePayload(BaseModel):
    operation_hash: str = Field(min_length=1)
    response: str = Field(min_length=1)
    stage: str = Field(default="confirmation", pattern="^(confirmation|step_up)$")
    challenge_id: uuid.UUID | None = None


class UiContext(BaseModel):
    """IDs may resolve an ellipsis, but never provide authoritative values."""

    selected_card_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    selected_account_id: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )


class RunAgentInput(BaseModel):
    """AG-UI RunAgentInput, trimmed to what this backend honors.

    `state` from the client is deliberately absent: graph state is server-owned
    (checkpointed), and accepting a client's version of it would let the browser
    edit the conversation's memory.
    """

    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = None
    messages: list[AguiMessage] = Field(default_factory=list)
    resume: ResumePayload | None = None
    context: UiContext | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _encode(event: AgentEvent) -> str | None:
    """Map application events to the AG-UI wire format (ag-ui.md event mapping).

    An unmapped event returns None rather than a guess — fail quiet on the wire,
    loud in the log.
    """
    match event:
        case RunStarted():
            return _sse("RUN_STARTED", {"threadId": event.thread_id, "runId": event.run_id})
        case TextDelta():
            return _sse(
                "TEXT_MESSAGE_CONTENT", {"messageId": event.message_id, "delta": event.delta}
            )
        case ConfirmationRequired():
            return _sse(
                "confirmation_required",
                {
                    "operationHash": event.operation_hash,
                    "operation": event.operation,
                    "currentAmount": event.current_amount,
                    "requestedAmount": event.requested_amount,
                    "expiresAt": event.expires_at,
                    "issuedAt": event.issued_at,
                    "recipientKeyMasked": event.recipient_key_masked,
                    "accountId": event.account_id,
                },
            )
        case StepUpRequired():
            return _sse(
                "step_up_required",
                {"operationHash": event.operation_hash, "expiresAt": event.expires_at},
            )
        case ToolCallStarted():
            return _sse(
                "TOOL_CALL_START",
                {
                    "toolCallId": event.tool_call_id,
                    "toolCallName": event.tool_name,
                    "args": event.args,
                },
            )
        case ToolCallEnded():
            return _sse(
                "TOOL_CALL_END",
                {"toolCallId": event.tool_call_id, "result": event.result_summary},
            )
        case CitationsEmitted():
            return _sse(
                "citations",
                {
                    "citations": [
                        {
                            "documentId": c.document_id,
                            "title": c.title,
                            "section": c.section,
                            "page": c.page,
                            "marker": c.marker(),
                        }
                        for c in event.citations
                    ]
                },
            )
        case StateSnapshot():
            return _sse(
                "STATE_SNAPSHOT",
                {
                    "snapshot": {
                        "route": event.route,
                        "intent": event.intent,
                        "pendingOperationHash": event.pending_operation_hash,
                        "dataChanged": event.data_changed,
                    }
                },
            )
        case RunFinished():
            return _sse(
                "RUN_FINISHED",
                {"threadId": event.thread_id, "runId": event.run_id, "route": event.route},
            )
        case RunError():
            return _sse(
                "RUN_ERROR", {"message": event.message, "correlationId": event.correlation_id}
            )
        case _:
            logger.warning("agui.unmapped_event", event_type=type(event).__name__)
            return None


def _last_user_message(body: RunAgentInput) -> str:
    for message in reversed(body.messages):
        if message.role == "user":
            return message.content
    return ""


@router.post("/agui")
async def agui(request: Request, body: RunAgentInput, user: CurrentUser) -> StreamingResponse:
    container = request.app.state.container
    conversation = request.app.state.conversation

    if not conversation.rate_limiter.allow(str(user.id)):
        raise ProblemError(
            status=429,
            title="Too Many Requests",
            detail="Muitas mensagens em pouco tempo. Aguarde um instante e tente de novo.",
        )

    message = _last_user_message(body)
    if body.resume is None and not message.strip():
        raise ProblemError(
            status=422, title="Unprocessable", detail="No user message in the run input"
        )

    await _guard_thread(request, body, user.id)

    session_factory = container.session_factory

    async def event_stream() -> AsyncIterator[str]:
        async with session_factory() as session, session.begin(), event_transaction(session):
            use_case = RunTurn(
                graph=conversation.graph,
                threads=PostgresThreadRepository(session, container.clock),
                events=conversation.providers.event_publisher,
                clock=container.clock,
                id_generator=container.id_generator,
                tracer=conversation.providers.tracer,
                banking=conversation.providers.banking,
            )
            if body.resume is not None:
                events = use_case.stream_resume(
                    ResumeTurnCommand(
                        user=user,
                        user_id=user.id,
                        thread_id=body.thread_id,
                        operation_hash=body.resume.operation_hash,
                        response=body.resume.response,
                        stage=body.resume.stage,
                        challenge_id=body.resume.challenge_id,
                    )
                )
            else:
                events = use_case.stream(
                    RunTurnCommand(
                        user=user,
                        user_id=user.id,
                        thread_id=body.thread_id,
                        message=message,
                        ui_context=body.context.model_dump(exclude_none=True)
                        if body.context
                        else None,
                    )
                )
            async for event in events:
                encoded = _encode(event)
                if encoded is not None:
                    yield encoded

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _guard_thread(request: Request, body: RunAgentInput, user_id: uuid.UUID) -> None:
    """Ownership decided before the stream opens: user B on user A's thread gets
    403, not a 200 with an apology inside (PRD006-FR-6)."""
    container = request.app.state.container
    conversation = request.app.state.conversation
    async with container.session_factory() as session, session.begin():
        use_case = RunTurn(
            graph=conversation.graph,
            threads=PostgresThreadRepository(session, container.clock),
            events=conversation.providers.event_publisher,
            clock=container.clock,
            id_generator=container.id_generator,
            tracer=conversation.providers.tracer,
            banking=conversation.providers.banking,
        )
        result = await use_case.authorize_thread(
            RunTurnCommand(user=None, user_id=user_id, thread_id=body.thread_id, message="")
        )
    if isinstance(result, Err):
        # Same answer for "not yours" and "doesn't exist" — no enumeration oracle.
        raise ProblemError(status=403, title="Forbidden", detail="Thread is not accessible")


class ConversationSummary(BaseModel):
    thread_id: str


class ConversationCitation(BaseModel):
    document_id: str
    title: str
    section: str
    page: int | None


class ConversationMessage(BaseModel):
    role: str
    content: str
    citations: list[ConversationCitation] = Field(default_factory=list)


class ConversationDetail(BaseModel):
    thread_id: str
    messages: list[ConversationMessage]


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(request: Request, user: CurrentUser) -> list[ConversationSummary]:
    container = request.app.state.container
    async with container.session_factory() as session:
        threads = await PostgresThreadRepository(session, container.clock).list_for_user(user.id)
    return [ConversationSummary(thread_id=t.thread_id) for t in threads]


@router.get("/conversations/{thread_id}", response_model=ConversationDetail)
async def get_conversation(
    thread_id: str, request: Request, user: CurrentUser
) -> ConversationDetail:
    """Return a user's persisted transcript without restoring any live interrupt.

    Ownership is checked against the durable thread binding *before* the
    checkpointer is queried.  Unlike a run request, a read must never claim a
    previously unseen thread id.
    """
    container = request.app.state.container
    async with container.session_factory() as session:
        thread = await PostgresThreadRepository(session, container.clock).get(thread_id)
    if thread is None or not thread.belongs_to(user.id):
        raise ProblemError(status=403, title="Forbidden", detail="Thread is not accessible")

    snapshot = await request.app.state.conversation.graph.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    messages = snapshot.values.get("messages", [])
    return ConversationDetail(
        thread_id=thread_id,
        messages=[
            ConversationMessage(
                role="user" if message.role is Role.USER else "assistant",
                content=message.content,
                citations=[
                    ConversationCitation(
                        document_id=citation.document_id,
                        title=citation.title,
                        section=citation.section,
                        page=citation.page,
                    )
                    for citation in getattr(message, "citations", ())
                ],
            )
            for message in messages
        ],
    )
