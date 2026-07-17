"""Admin-only, read-only audit trail endpoints (PRD-009)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from api.dependencies import CurrentUser
from api.problem import ProblemError
from audit.adapters.outbound.postgres.audit_log_repository import PostgresAuditLogRepository
from audit.application.dto import AuditPage, AuditQuery
from audit.application.use_cases.query_audit import QueryAudit
from audit.domain.entities import AuditEvent
from identity_access.adapters.outbound.postgres.user_repository import PostgresUserRepository
from identity_access.application.dto import AuthorizationRequest
from identity_access.application.use_cases.authorize_action import AuthorizeAction
from identity_access.domain.authorization import Action, AuthorizationService, Permit
from identity_access.domain.entities import User
from shared.adapters.event_publisher import event_transaction

router = APIRouter(prefix="/api/admin")


class AuditActorResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str

    @classmethod
    def from_domain(cls, user: User) -> "AuditActorResponse":
        return cls(id=user.id, name=user.name, email=user.email, role=user.role.value)


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    user_ref: str
    action: str
    amount: Decimal | None
    occurred_at: datetime
    resource: str
    outcome: str
    trace_id: str | None
    details: dict[str, Any]
    actor: AuditActorResponse | None

    @classmethod
    def from_domain(
        cls, event: AuditEvent, *, actors: dict[str, User] | None = None
    ) -> "AuditEventResponse":
        actor = (actors or {}).get(event.user_ref)
        return cls(
            id=event.id,
            event_id=event.event_id,
            user_ref=event.user_ref,
            action=event.action,
            amount=event.amount,
            occurred_at=event.occurred_at,
            resource=event.resource,
            outcome=event.outcome,
            trace_id=event.trace_id,
            details=dict(event.details),
            actor=AuditActorResponse.from_domain(actor) if actor is not None else None,
        )


def _utc(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProblemError(
            status=422, title="Unprocessable", detail=f"{field_name} must include a UTC offset"
        )
    return value.astimezone(UTC)


async def _require_view_audit(request: Request, user: CurrentUser) -> None:
    """Commit an authorization-denial event before returning the HTTP 403."""
    container = request.app.state.container
    identity = request.app.state.identity
    async with container.session_factory() as session, session.begin(), event_transaction(session):
        decision = await AuthorizeAction(
            service=AuthorizationService(),
            clock=container.clock,
            id_generator=container.id_generator,
            event_publisher=identity.event_publisher,
        ).execute(AuthorizationRequest(user=user, action=Action.VIEW_AUDIT))
    if not isinstance(decision, Permit):
        raise ProblemError(status=403, title="Forbidden", detail="Audit trail is not accessible")


async def _actors_for_events(
    users: PostgresUserRepository, events: tuple[AuditEvent, ...]
) -> dict[str, User]:
    actor_ids: list[uuid.UUID] = []
    for event in events:
        try:
            actor_ids.append(uuid.UUID(event.user_ref))
        except ValueError:
            continue
    actors = await users.list_by_ids(actor_ids)
    return {str(actor.id): actor for actor in actors}


@router.get("/audit", response_model=list[AuditEventResponse])
async def list_audit(
    request: Request,
    response: Response,
    user: CurrentUser,
    user_search: Annotated[str | None, Query(alias="user")] = None,
    action: str | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditEventResponse]:
    await _require_view_audit(request, user)
    query = AuditQuery(
        action=action,
        from_at=_utc(from_at, field_name="from"),
        to_at=_utc(to_at, field_name="to"),
        page=page,
        page_size=page_size,
    )
    try:
        QueryAudit.validate(query)
    except ValueError as exc:
        raise ProblemError(status=422, title="Unprocessable", detail=str(exc)) from exc

    container = request.app.state.container
    async with container.session_factory() as session:
        users = PostgresUserRepository(session)
        if user_search is not None:
            targets = await users.search_for_audit(user_search)
            if not targets:
                audit_page = AuditPage(items=(), total=0)
            else:
                query = AuditQuery(
                    user_refs=tuple(str(target.id) for target in targets),
                    action=query.action,
                    from_at=query.from_at,
                    to_at=query.to_at,
                    page=query.page,
                    page_size=query.page_size,
                )
                audit_page = await QueryAudit(repository=PostgresAuditLogRepository(session)).list(
                    query
                )
        else:
            audit_page = await QueryAudit(repository=PostgresAuditLogRepository(session)).list(
                query
            )
        actors = await _actors_for_events(users, audit_page.items)

    response.headers["X-Total-Count"] = str(audit_page.total)
    return [AuditEventResponse.from_domain(event, actors=actors) for event in audit_page.items]


@router.get("/audit/{audit_id}", response_model=AuditEventResponse)
async def get_audit(audit_id: uuid.UUID, request: Request, user: CurrentUser) -> AuditEventResponse:
    await _require_view_audit(request, user)
    container = request.app.state.container
    async with container.session_factory() as session:
        event = await QueryAudit(repository=PostgresAuditLogRepository(session)).get(audit_id)
        actors = await _actors_for_events(
            PostgresUserRepository(session), (event,) if event else ()
        )
    if event is None:
        raise ProblemError(status=404, title="Not Found", detail="Audit event was not found")
    return AuditEventResponse.from_domain(event, actors=actors)
