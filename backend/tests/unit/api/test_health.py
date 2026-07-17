from types import SimpleNamespace

import httpx
import pytest

from api.app import create_app, wait_for_database
from api.routers.health import _chroma_heartbeat, _provider_configured
from shared.config import Settings
from shared.container import Container
from tests.fakes import FakeEngine

SETTINGS = Settings(
    _env_file=None,
    env="test",
    database_url="postgresql+asyncpg://app:app@localhost:5432/app",
    jwt_secret="test-secret",
)


def _app(engine: FakeEngine):
    container = Container.build(SETTINGS, engine=engine)
    return create_app(settings=SETTINGS, container=container)


async def _get_health(engine: FakeEngine) -> httpx.Response:
    app = _app(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


async def test_health_ok_when_database_up() -> None:
    response = await _get_health(FakeEngine(healthy=True))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "components": {"database": "ok"}}


async def test_health_503_when_database_down() -> None:
    response = await _get_health(FakeEngine(healthy=False))
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["database"] == "unreachable"


async def test_startup_crashes_after_bounded_retries() -> None:
    engine = FakeEngine(healthy=False)
    with pytest.raises(RuntimeError, match="refusing to start"):
        await wait_for_database(engine, attempts=2, delay_seconds=0)


async def test_docs_hidden_outside_local() -> None:
    app = _app(FakeEngine())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 404


async def test_chroma_heartbeat_uses_the_public_health_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    def probe(url: str, *, timeout: int) -> None:
        called.update(url=url, timeout=timeout)

    monkeypatch.setattr("api.routers.health.urlopen", probe)
    await _chroma_heartbeat("http://chroma:8000/")
    assert called == {"url": "http://chroma:8000/api/v2/heartbeat", "timeout": 2}


def test_provider_health_is_configuration_sanity_not_a_network_probe() -> None:
    settings = SETTINGS.model_copy(update={"llm_fallback_order": "gemini", "gemini_api_key": ""})
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(settings=settings)))
    )
    assert _provider_configured(request) is False
