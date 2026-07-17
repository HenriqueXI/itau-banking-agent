"""Anonymous/invalid-token rejection on protected routes (PRD004-FR-3).

These 401 paths never touch the database, so a FakeEngine suffices.
"""

import httpx

from api.app import create_app
from shared.config import Settings
from shared.container import Container
from tests.fakes import FakeEngine

SETTINGS = Settings(
    _env_file=None,
    env="test",
    database_url="postgresql+asyncpg://app:app@localhost:5432/app",
    jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
)


def _client() -> httpx.AsyncClient:
    container = Container.build(SETTINGS, engine=FakeEngine())
    app = create_app(settings=SETTINGS, container=container)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_missing_token_gets_problem_json_401() -> None:
    async with _client() as client:
        response = await client.post("/api/auth/step-up/request", json={"operation_hash": "op-1"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["title"] == "Unauthorized"
    assert body["status"] == 401
    assert "correlation_id" in body


async def test_garbage_token_rejected() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/auth/step-up/request",
            json={"operation_hash": "op-1"},
            headers={"Authorization": "Bearer not.a.jwt"},
        )
    assert response.status_code == 401


async def test_non_bearer_scheme_rejected() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/auth/step-up/request",
            json={"operation_hash": "op-1"},
            headers={"Authorization": "Basic YW5hOmRlbW8="},
        )
    assert response.status_code == 401
