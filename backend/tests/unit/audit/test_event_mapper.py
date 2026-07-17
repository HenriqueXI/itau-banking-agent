"""Audit event mapping is catalog-driven and framework-free."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.fakes.providers import SequentialIdGenerator

from audit.application.event_mapper import UnsupportedAuditEventError, map_event
from shared.domain.outbox import StoredEvent


def event(event_type: str, payload: dict[str, object], **overrides: object) -> StoredEvent:
    version = int(overrides.pop("version", 1))
    actor_user_id = overrides.pop("actor_user_id", "user-1")
    trace_id = overrides.pop("trace_id", "trace-1")
    return StoredEvent(
        event_id=uuid.UUID(int=10),
        event_type=event_type,
        version=version,
        occurred_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        payload=payload,
        **overrides,
    )


@pytest.mark.parametrize(
    ("stored", "action", "outcome", "resource", "amount"),
    [
        (
            event(
                "identity.AuthorizationDenied",
                {"attempted_action": "view_audit", "reason": "role_forbidden"},
            ),
            "AUTHORIZATION_DENIED",
            "denied",
            "action:view_audit",
            None,
        ),
        (
            event(
                "identity.StepUpIssued",
                {"challenge_id": "masked", "operation_hash": "op-1", "expires_at": "x"},
            ),
            "STEP_UP",
            "issued",
            "operation:op-1",
            None,
        ),
        (
            event("identity.StepUpSucceeded", {"challenge_id": "masked", "operation_hash": "op-1"}),
            "STEP_UP",
            "succeeded",
            "operation:op-1",
            None,
        ),
        (
            event(
                "identity.StepUpFailed",
                {"challenge_id": "masked", "operation_hash": "op-1", "reason": "invalid_code"},
            ),
            "STEP_UP",
            "failed",
            "operation:op-1",
            None,
        ),
        (
            event(
                "conversation.GuardrailTriggered",
                {
                    "thread_id": "thread-1",
                    "check_id": "I1",
                    "disposition": "blocked",
                    "ring": "input",
                },
            ),
            "GUARDRAIL_TRIGGERED",
            "blocked",
            "conversation:thread-1",
            None,
        ),
        (
            event(
                "conversation.ConversationTurnCompleted",
                {
                    "thread_id": "thread-1",
                    "intent": "smalltalk",
                    "route": "smalltalk",
                    "citation_count": 0,
                    "provider": "ollama",
                },
            ),
            "CONVERSATION_TURN",
            "completed",
            "conversation:thread-1",
            None,
        ),
        (
            event(
                "knowledge.DocumentIngested",
                {
                    "document_id": "doc-1",
                    "title": "FAQ",
                    "source_type": "markdown",
                    "document_version": 1,
                    "chunk_count": 3,
                    "content_hash": "hash",
                },
            ),
            "KNOWLEDGE_DOCUMENT_INGESTION",
            "ingested",
            "document:doc-1",
            None,
        ),
        (
            event(
                "banking.CardLimitChanged",
                {"card_id": "card-1", "operation_hash": "op-1", "new_limit": "15000"},
            ),
            "CARD_LIMIT_CHANGE",
            "changed",
            "card:card-1",
            Decimal("15000"),
        ),
        (
            event(
                "banking.OperationConfirmationExpired",
                {"operation_hash": "op-1", "tool": "alterar_limite"},
            ),
            "OPERATION_CONFIRMATION",
            "expired",
            "operation:op-1",
            None,
        ),
    ],
)
def test_maps_each_registered_event(
    stored: StoredEvent, action: str, outcome: str, resource: str, amount: Decimal | None
) -> None:
    audit_event = map_event(stored, id_generator=SequentialIdGenerator())

    assert audit_event.action == action
    assert audit_event.outcome == outcome
    assert audit_event.resource == resource
    assert audit_event.amount == amount
    assert audit_event.user_ref == "user-1"
    assert audit_event.trace_id == "trace-1"


def test_masks_details_and_marks_events_without_actor_or_trace_as_system() -> None:
    stored = event(
        "identity.AuthorizationDenied",
        {"attempted_action": "view_audit", "reason": "ana@demo.com"},
        actor_user_id=None,
        trace_id=None,
    )

    audit_event = map_event(stored, id_generator=SequentialIdGenerator())

    assert audit_event.user_ref == "system"
    assert audit_event.trace_id is None
    assert audit_event.details["reason"] == "ana****"


def test_rejects_unknown_event_type_or_version() -> None:
    with pytest.raises(UnsupportedAuditEventError):
        map_event(event("banking.UnknownEvent", {}), id_generator=SequentialIdGenerator())
    with pytest.raises(UnsupportedAuditEventError):
        map_event(
            event("identity.AuthorizationDenied", {}, version=2),
            id_generator=SequentialIdGenerator(),
        )
