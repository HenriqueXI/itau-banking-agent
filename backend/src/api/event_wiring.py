"""Composition-root registrations for v1 domain events."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit.adapters.inbound.event_handler import AuditEventHandler
from audit.application.event_mapper import supported_event_versions
from shared.adapters.event_bus import InMemoryEventBus
from shared.application.ports.id_generator import IdGenerator


def build_event_bus(
    *, session_factory: async_sessionmaker[AsyncSession], id_generator: IdGenerator
) -> InMemoryEventBus:
    bus = InMemoryEventBus()
    handler = AuditEventHandler(session_factory=session_factory, id_generator=id_generator)
    supported = supported_event_versions()
    for event_type, version in supported:
        bus.register(event_type=event_type, version=version, handler=handler.handle)
    bus.assert_complete(supported)
    return bus
