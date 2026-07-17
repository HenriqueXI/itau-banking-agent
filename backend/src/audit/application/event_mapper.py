"""Stable mapping from outbox envelopes to audit records."""

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from audit.domain.entities import AuditEvent
from shared.application.ports.id_generator import IdGenerator
from shared.domain.outbox import StoredEvent

AuditMapping = Callable[[StoredEvent], tuple[str, str, str, dict[str, Any]]]


class UnsupportedAuditEventError(ValueError):
    """Raised so the relay retries/dead-letters an event without an audit decision."""


def _payload(event: StoredEvent, name: str, default: str = "unknown") -> str:
    value = event.payload.get(name, default)
    return str(value) if value is not None else default


def _authorization_denied(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    attempted_action = _payload(event, "attempted_action")
    target = _payload(event, "target_resource", "")
    return (
        "AUTHORIZATION_DENIED",
        "denied",
        f"customer:{target}" if target else f"action:{attempted_action}",
        {"attempted_action": attempted_action, "reason": _payload(event, "reason")},
    )


def _step_up(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    outcomes = {
        "identity.StepUpIssued": "issued",
        "identity.StepUpSucceeded": "succeeded",
        "identity.StepUpFailed": "failed",
    }
    details: dict[str, Any] = {"challenge_id": event.payload.get("challenge_id")}
    if event.event_type == "identity.StepUpIssued":
        details["expires_at"] = event.payload.get("expires_at")
    if event.event_type == "identity.StepUpFailed":
        details["reason"] = event.payload.get("reason")
    return (
        "STEP_UP",
        outcomes[event.event_type],
        f"operation:{_payload(event, 'operation_hash')}",
        details,
    )


def _guardrail(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    return (
        "GUARDRAIL_TRIGGERED",
        _payload(event, "disposition"),
        f"conversation:{_payload(event, 'thread_id')}",
        {"check_id": event.payload.get("check_id"), "ring": event.payload.get("ring")},
    )


def _turn_completed(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    return (
        "CONVERSATION_TURN",
        "completed",
        f"conversation:{_payload(event, 'thread_id')}",
        {
            "intent": event.payload.get("intent"),
            "route": event.payload.get("route"),
            "citation_count": event.payload.get("citation_count"),
            "provider": event.payload.get("provider"),
        },
    )


def _document_ingested(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    return (
        "KNOWLEDGE_DOCUMENT_INGESTION",
        "ingested",
        f"document:{_payload(event, 'document_id')}",
        {
            "source_type": event.payload.get("source_type"),
            "document_version": event.payload.get("document_version"),
            "chunk_count": event.payload.get("chunk_count"),
            "content_hash": event.payload.get("content_hash"),
        },
    )


def _card_limit(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    outcomes = {
        "banking.CardLimitChangeRequested": "requested",
        "banking.CardLimitChangeDenied": "denied",
        "banking.CardLimitChanged": "changed",
        "banking.CardLimitChangeFailed": "failed",
    }
    return (
        "CARD_LIMIT_CHANGE",
        outcomes[event.event_type],
        f"card:{_payload(event, 'card_id')}",
        dict(event.payload),
    )


def _confirmation(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    outcomes = {
        "banking.OperationConfirmationRequested": "requested",
        "banking.OperationConfirmationCancelled": "cancelled",
        "banking.OperationConfirmationExpired": "expired",
    }
    return (
        "OPERATION_CONFIRMATION",
        outcomes[event.event_type],
        f"operation:{_payload(event, 'operation_hash')}",
        dict(event.payload),
    )


def _pix(event: StoredEvent) -> tuple[str, str, str, dict[str, Any]]:
    outcomes = {
        "banking.PixTransferDenied": "denied",
        "banking.PixTransferExecuted": "executed",
        "banking.PixTransferFailed": "failed",
    }
    return (
        "PIX",
        outcomes[event.event_type],
        f"account:{_payload(event, 'account_id', _payload(event, 'customer_id'))}",
        dict(event.payload),
    )


EVENT_MAPPINGS: dict[tuple[str, int], AuditMapping] = {
    ("identity.AuthorizationDenied", 1): _authorization_denied,
    ("identity.StepUpIssued", 1): _step_up,
    ("identity.StepUpSucceeded", 1): _step_up,
    ("identity.StepUpFailed", 1): _step_up,
    ("conversation.GuardrailTriggered", 1): _guardrail,
    ("conversation.ConversationTurnCompleted", 1): _turn_completed,
    ("knowledge.DocumentIngested", 1): _document_ingested,
    ("banking.CardLimitChangeRequested", 1): _card_limit,
    ("banking.CardLimitChangeDenied", 1): _card_limit,
    ("banking.CardLimitChanged", 1): _card_limit,
    ("banking.CardLimitChangeFailed", 1): _card_limit,
    ("banking.OperationConfirmationRequested", 1): _confirmation,
    ("banking.OperationConfirmationCancelled", 1): _confirmation,
    ("banking.OperationConfirmationExpired", 1): _confirmation,
    ("banking.PixTransferDenied", 1): _pix,
    ("banking.PixTransferExecuted", 1): _pix,
    ("banking.PixTransferFailed", 1): _pix,
}

AUDIT_EVENT_CATALOG: tuple[tuple[str, int], ...] = (
    ("identity.AuthorizationDenied", 1),
    ("identity.StepUpIssued", 1),
    ("identity.StepUpSucceeded", 1),
    ("identity.StepUpFailed", 1),
    ("conversation.GuardrailTriggered", 1),
    ("conversation.ConversationTurnCompleted", 1),
    ("knowledge.DocumentIngested", 1),
    ("banking.CardLimitChangeRequested", 1),
    ("banking.CardLimitChangeDenied", 1),
    ("banking.CardLimitChanged", 1),
    ("banking.CardLimitChangeFailed", 1),
    ("banking.OperationConfirmationRequested", 1),
    ("banking.OperationConfirmationCancelled", 1),
    ("banking.OperationConfirmationExpired", 1),
    ("banking.PixTransferDenied", 1),
    ("banking.PixTransferExecuted", 1),
    ("banking.PixTransferFailed", 1),
)


def supported_event_versions() -> tuple[tuple[str, int], ...]:
    missing = [event for event in AUDIT_EVENT_CATALOG if event not in EVENT_MAPPINGS]
    if missing:
        raise RuntimeError(f"Audit mapping missing for event versions: {missing!r}")
    return AUDIT_EVENT_CATALOG


def map_event(event: StoredEvent, *, id_generator: IdGenerator) -> AuditEvent:
    mapping = EVENT_MAPPINGS.get((event.event_type, event.version))
    if mapping is None:
        raise UnsupportedAuditEventError(f"No audit mapping for {event.event_type}@{event.version}")
    action, outcome, resource, details = mapping(event)
    return AuditEvent(
        id=id_generator.new_id(),
        event_id=event.event_id,
        user_ref=event.actor_user_id or "system",
        action=action,
        amount=_amount(event),
        occurred_at=event.occurred_at,
        resource=resource,
        outcome=outcome,
        trace_id=event.trace_id,
        details=details,
    )


def _amount(event: StoredEvent) -> Decimal | None:
    """Extract the auditable amount without making generic events monetary."""
    raw = event.payload.get(
        "amount", event.payload.get("new_limit", event.payload.get("requested_limit"))
    )
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
