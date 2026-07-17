"""AuthorizeAction: run the pure decision, emit AuthorizationDenied on refusal.

The decision itself is pure (``AuthorizationService``); this thin seam publishes
the deny event — via the transactional outbox once PRD-014 lands — before the
caller touches any port, so a denied request never reaches a data fetch
(security.md §3). Callers must route through here, not the domain service
directly; the choke-point architecture test (PRD-015) will enforce it.
"""

from identity_access.application.dto import AuthorizationRequest
from identity_access.domain.authorization import AuthorizationService, Decision, Deny
from identity_access.domain.events import AuthorizationDenied
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


class AuthorizeAction:
    def __init__(
        self,
        *,
        service: AuthorizationService,
        clock: Clock,
        id_generator: IdGenerator,
        event_publisher: EventPublisher,
    ) -> None:
        self._service = service
        self._clock = clock
        self._ids = id_generator
        self._events = event_publisher

    async def execute(self, request: AuthorizationRequest) -> Decision:
        decision = self._service.authorize(request.user, request.action, request.resource)
        if isinstance(decision, Deny):
            await self._events.publish(
                AuthorizationDenied(
                    event_id=self._ids.new_id(),
                    occurred_at=self._clock.now(),
                    actor_user_id=str(request.user.id),
                    attempted_action=request.action.value,
                    target_resource=request.resource.owner_id if request.resource else None,
                    reason=decision.reason.value,
                )
            )
        return decision
