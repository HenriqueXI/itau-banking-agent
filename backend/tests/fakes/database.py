"""Fake async engine — enough surface for /health and lifespan wiring tests."""

from typing import Any


class _FakeConnection:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def execute(self, statement: Any) -> None:
        if not self._healthy:
            raise ConnectionError("fake database is down")


class _FakeConnectionContext:
    def __init__(self, engine: "FakeEngine") -> None:
        self._engine = engine

    async def __aenter__(self) -> _FakeConnection:
        if not self._engine.healthy:
            raise ConnectionError("fake database is down")
        return _FakeConnection(self._engine.healthy)

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeEngine:
    """Duck-types the AsyncEngine surface the app uses (connect/dispose)."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.disposed = False

    def connect(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self)

    async def dispose(self) -> None:
        self.disposed = True
