"""Composed-stack fixtures for the banking e2e suite (PRD-007 / PRD-008).

Real pieces: PostgreSQL (testcontainers), the LangGraph checkpointer, the MCP
protocol client against an in-process FastMCP simulator (injected so tests can
read its call log — the UC-3 zero-call proof), the authorization matrix, and
the transactional outbox. Fake: the LLM (ScriptedLlm) and the knowledge base.
"""

import asyncio
import json
import socket
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from api.app import ConversationRuntime, checkpointer_dsn, create_app
from api.banking_wiring import BankingWorkflowAdapter
from api.conversation_wiring import IdentityAuthorizationAdapter
from api.event_wiring import build_event_bus
from api.rate_limit import SlidingWindowRateLimiter
from audit.adapters.outbound.postgres.tables import audit_events
from banking.adapters.outbound.mcp_client import McpBankingSystemsClient
from banking.adapters.outbound.postgres.tables import (
    pending_operations,
    pix_daily_buckets,
    pix_transfers,
)
from conversation.adapters.outbound.demo_customer_reference import DemoCustomerReferenceResolver
from conversation.application.graph.builder import build_graph
from conversation.application.graph.dependencies import GraphConfig, GraphDependencies
from identity_access.adapters.outbound.postgres.tables import users
from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
from identity_access.application.use_cases.authorize_action import AuthorizeAction
from identity_access.domain.authorization import AuthorizationService
from mcp_server.main import create_server
from mcp_server.simulator import CoreBankingSimulator
from shared.adapters.event_publisher import PostgresEventPublisher
from shared.adapters.outbox_relay import OutboxRelay
from shared.config import Settings
from shared.container import Container
from tests.fakes.conversation import FakeConversationProviders, StubRetrieval

JWT_SECRET = "integration-secret-0123456789abcdef0123456789ab"
ANA_ID = uuid.UUID("00000000-0000-0000-0000-00000000ba1a")
BRUNO_ID = uuid.UUID("00000000-0000-0000-0000-00000000bb1b")
PASSWORD = "demo123"


class McpServerHandle:
    """The simulated core banking as a stoppable in-process server."""

    def __init__(self, url: str, server: uvicorn.Server, thread: threading.Thread) -> None:
        self.url = url
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def simulator() -> CoreBankingSimulator:
    return CoreBankingSimulator()


@pytest.fixture
async def mcp_server(simulator: CoreBankingSimulator):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_server(simulator).streamable_http_app(),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    handle = McpServerHandle(f"{base_url}/mcp", server, thread)
    try:
        async with httpx.AsyncClient() as probe:
            for _ in range(50):
                try:
                    if (await probe.get(f"{base_url}/health")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("MCP simulator did not start")
        yield handle
    finally:
        handle.stop()


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Per-module Settings overrides (e.g. a higher PIX daily limit for UC-4)."""
    return {}


@pytest.fixture
def settings(migrated_database: dict[str, str], settings_overrides: dict[str, Any]) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url=migrated_database["asyncpg"],
        jwt_secret=JWT_SECRET,
        **settings_overrides,
    )


@pytest.fixture
async def container(settings: Settings):
    container = Container.build(settings)
    hasher = Argon2PasswordHasher()
    async with container.engine.begin() as connection:
        for user_id, email, role, customer_id in (
            (ANA_ID, "ana-banking@demo", "customer", "123"),
            (BRUNO_ID, "bruno-banking@demo", "manager", "456"),
        ):
            statement = insert(users).values(
                id=user_id,
                email=email,
                name=email.split("@")[0].title(),
                role=role,
                customer_id=customer_id,
                password_hash=hasher.hash(PASSWORD),
                created_at=datetime.now(UTC),
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[users.c.email],
                    set_={
                        "password_hash": statement.excluded.password_hash,
                        "role": statement.excluded.role,
                        "customer_id": statement.excluded.customer_id,
                    },
                )
            )
    yield container
    await container.aclose()


@pytest.fixture(autouse=True)
async def clean_banking_state(container: Container) -> None:
    """Keep hash-bound banking scenarios independent within the shared test DB."""
    async with container.session_factory() as session, session.begin():
        await session.execute(delete(pix_transfers))
        await session.execute(delete(pix_daily_buckets))
        await session.execute(delete(pending_operations))


@pytest.fixture
async def runtime(
    settings: Settings, container: Container, mcp_server: McpServerHandle, scripted_llm
):
    """The PRD-007/008 composition: real graph + checkpointer + MCP adapter +
    authorization matrix; scripted LLM (each test module declares its rules)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_dsn(settings.database_url)
    ) as checkpointer:
        await checkpointer.setup()
        events = PostgresEventPublisher()
        authorization = IdentityAuthorizationAdapter(
            AuthorizeAction(
                service=AuthorizationService(),
                clock=container.clock,
                id_generator=container.id_generator,
                event_publisher=events,
            )
        )
        banking_adapter = BankingWorkflowAdapter(
            client=McpBankingSystemsClient(url=mcp_server.url),
            settings=settings,
            clock=container.clock,
            id_generator=container.id_generator,
            authorization=authorization,
        )
        deps = GraphDependencies(
            llm=scripted_llm,
            retrieval=StubRetrieval(),
            authorization=authorization,
            customer_references=DemoCustomerReferenceResolver(),
            events=events,
            clock=container.clock,
            id_generator=container.id_generator,
            config=GraphConfig(grounding_judge_enabled=False),
            banking=banking_adapter,
        )
        yield ConversationRuntime(
            providers=FakeConversationProviders(events, banking=banking_adapter),
            graph=build_graph(deps, checkpointer=checkpointer),
            rate_limiter=SlidingWindowRateLimiter(clock=container.clock, max_per_minute=1000),
        )


@pytest.fixture
async def client(settings: Settings, container: Container, runtime: ConversationRuntime):
    app = create_app(settings=settings, container=container, conversation=runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def login(client: httpx.AsyncClient, email: str = "ana-banking@demo") -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def run_input(
    thread_id: str, message: str, *, context: dict[str, str] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "messages": [{"role": "user", "content": message}],
    }
    if context is not None:
        payload["context"] = context
    return payload


def resume_input(
    thread_id: str,
    operation_hash: str,
    response: str,
    *,
    stage: str = "confirmation",
    challenge_id: str | None = None,
) -> dict[str, Any]:
    resume: dict[str, Any] = {
        "operation_hash": operation_hash,
        "response": response,
        "stage": stage,
    }
    if challenge_id is not None:
        resume["challenge_id"] = challenge_id
    return {"thread_id": thread_id, "resume": resume}


async def stream(client: httpx.AsyncClient, token: str, body: dict[str, Any]) -> str:
    async with client.stream(
        "POST", "/api/agui", json=body, headers={"Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200
        return "".join([chunk async for chunk in response.aiter_text()])


def sse_events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE stream into (event, payload) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    for line in raw.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ").strip()
        elif line.startswith("data: ") and name is not None:
            events.append((name, json.loads(line.removeprefix("data: "))))
            name = None
    return events


def event_payload(raw: str, event_name: str) -> dict[str, Any] | None:
    for name, payload in sse_events(raw):
        if name == event_name:
            return payload
    return None


def stream_text(raw: str) -> str:
    return "".join(
        payload["delta"] for name, payload in sse_events(raw) if name == "TEXT_MESSAGE_CONTENT"
    )


async def deliver_outbox(container: Container) -> None:
    """Run one relay pass so outbox events become audit rows."""
    relay = OutboxRelay(
        session_factory=container.session_factory,
        event_bus=build_event_bus(
            session_factory=container.session_factory, id_generator=container.id_generator
        ),
        clock=container.clock,
        batch_size=200,
        max_attempts=5,
        max_backoff_seconds=60,
    )
    await relay.run_once()


async def audit_rows(
    container: Container, *, action: str, outcome: str | None = None, user_ref: str | None = None
) -> list[Any]:
    query = select(audit_events).where(audit_events.c.action == action)
    if outcome is not None:
        query = query.where(audit_events.c.outcome == outcome)
    if user_ref is not None:
        query = query.where(audit_events.c.user_ref == user_ref)
    async with container.engine.connect() as connection:
        return list((await connection.execute(query)).all())
