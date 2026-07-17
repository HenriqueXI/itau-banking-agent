"""Composition root: hand-rolled DI container (PRD-001 open question #1).

Hand-rolled over `dependency-injector`: providers are plain attributes (no
wiring magic), async resources are explicit, and tests override by passing
fakes to `build()` or via `with_overrides()`. Only this container and `api/`
may wire adapters to ports (backend/README.md rule 4).
"""

from dataclasses import dataclass, replace
from typing import Any, Self

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.adapters.clock import SystemClock
from shared.adapters.id_generator import UuidIdGenerator
from shared.application.ports.clock import Clock
from shared.application.ports.id_generator import IdGenerator
from shared.config import Settings


@dataclass(frozen=True)
class Container:
    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def build(cls, settings: Settings, **overrides: Any) -> Self:
        """Wire default adapters; `overrides` replaces any provider by name."""
        engine: AsyncEngine = overrides.get(
            "engine", create_async_engine(settings.database_url, pool_pre_ping=True)
        )
        defaults: dict[str, Any] = {
            "clock": SystemClock(),
            "id_generator": UuidIdGenerator(),
            "engine": engine,
            "session_factory": async_sessionmaker(engine, expire_on_commit=False),
        }
        defaults.update(overrides)
        return cls(settings=settings, **defaults)

    def with_overrides(self, **overrides: Any) -> "Container":
        """Copy of the container with providers replaced — for tests."""
        return replace(self, **overrides)

    async def aclose(self) -> None:
        await self.engine.dispose()
