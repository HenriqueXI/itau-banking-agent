"""POST /api/agui end to end against a real migrated PostgreSQL + checkpointer.

The LLM and the knowledge base are fakes (no quota, no network, NFR-7); what's
real here is the wire contract, the thread binding, and the checkpointed memory
that survives a process restart (US-2.2).
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.dialects.postgresql import insert

from api.app import ConversationRuntime, checkpointer_dsn, create_app
from api.rate_limit import SlidingWindowRateLimiter
from conversation.adapters.outbound.demo_customer_reference import DemoCustomerReferenceResolver
from conversation.application.graph.builder import build_graph
from conversation.application.graph.dependencies import GraphConfig, GraphDependencies
from identity_access.adapters.outbound.postgres.tables import users
from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
from shared.config import Settings
from shared.container import Container
from tests.fakes.conversation import (
    FakeConversationProviders,
    ScriptedLlm,
    StubAuthorization,
    StubRetrieval,
    evidence,
    grounded,
    understand_json,
)
from tests.fakes.providers import RecordingEventPublisher

pytestmark = pytest.mark.integration

JWT_SECRET = "integration-secret-0123456789abcdef0123456789ab"
ANA_ID = uuid.UUID("00000000-0000-0000-0000-000000000a1a")
BRUNO_ID = uuid.UUID("00000000-0000-0000-0000-000000000b1b")
PASSWORD = "demo123"
MARKER = "【Tarifas 2026 — Consignado】"


@pytest.fixture(scope="session")
def settings(migrated_database: dict[str, str]) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url=migrated_database["asyncpg"],
        jwt_secret=JWT_SECRET,
    )


@pytest.fixture
async def container(settings: Settings):
    container = Container.build(settings)
    hasher = Argon2PasswordHasher()
    async with container.engine.begin() as connection:
        for user_id, email, customer_id in (
            (ANA_ID, "ana@demo", "123"),
            (BRUNO_ID, "bruno@demo", "456"),
        ):
            statement = insert(users).values(
                id=user_id,
                email=email,
                name=email.split("@")[0].title(),
                role="customer",
                customer_id=customer_id,
                password_hash=hasher.hash(PASSWORD),
                created_at=datetime.now(UTC),
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[users.c.email],
                    set_={"password_hash": statement.excluded.password_hash},
                )
            )
    yield container
    await container.aclose()


def _scripted_llm() -> ScriptedLlm:
    return ScriptedLlm(
        [
            (
                "Mensagem do usuário",
                understand_json(intent="kb_query", params={"query": "taxa do consignado"}),
            ),
            ("Evidências", f"A taxa do consignado para aposentados é 1,49% a.m. {MARKER}"),
        ],
        default="Posso ajudar com assuntos do banco.",
    )


@pytest.fixture
async def runtime(settings: Settings, container: Container):
    """A real Postgres checkpointer under a fake LLM — the composition the
    acceptance criteria actually care about."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_dsn(settings.database_url)
    ) as checkpointer:
        await checkpointer.setup()
        deps = GraphDependencies(
            llm=_scripted_llm(),
            retrieval=StubRetrieval(
                grounded(evidence("Consignado para aposentados: taxa de 1,49% a.m."))
            ),
            authorization=StubAuthorization(),
            customer_references=DemoCustomerReferenceResolver(),
            events=RecordingEventPublisher(),
            clock=container.clock,
            id_generator=container.id_generator,
            config=GraphConfig(grounding_judge_enabled=False),
        )
        yield ConversationRuntime(
            providers=FakeConversationProviders(RecordingEventPublisher()),
            graph=build_graph(deps, checkpointer=checkpointer),
            rate_limiter=SlidingWindowRateLimiter(clock=container.clock, max_per_minute=30),
        )


@pytest.fixture
async def client(settings: Settings, container: Container, runtime: ConversationRuntime):
    app = create_app(settings=settings, container=container, conversation=runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def login(client: httpx.AsyncClient, email: str = "ana@demo") -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def _run_input(thread_id: str, message: str) -> dict:
    return {"thread_id": thread_id, "messages": [{"role": "user", "content": message}]}


async def _stream(client: httpx.AsyncClient, token: str, body: dict) -> str:
    async with client.stream(
        "POST", "/api/agui", json=body, headers={"Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return "".join([chunk async for chunk in response.aiter_text()])


async def test_kb_turn_streams_agui_events_with_citations(client: httpx.AsyncClient) -> None:
    """UC-1 over the wire: tool events, text deltas, and a citations payload."""
    token = await login(client)
    stream = await _stream(
        client, token, _run_input("t-uc1", "Qual a taxa do consignado para aposentados?")
    )

    assert "event: RUN_STARTED" in stream
    assert "event: TOOL_CALL_START" in stream
    assert "event: TEXT_MESSAGE_CONTENT" in stream
    assert "event: citations" in stream
    assert "event: RUN_FINISHED" in stream
    assert "1,49" in stream
    assert "Tarifas 2026" in stream


async def test_thread_is_bound_to_its_first_user(client: httpx.AsyncClient) -> None:
    """PRD006-FR-6: user B on user A's thread is rejected before any stream —
    and the refusal never says whether the thread exists."""
    ana_token = await login(client, "ana@demo")
    await _stream(client, ana_token, _run_input("t-owned", "Qual a taxa do consignado?"))

    bruno_token = await login(client, "bruno@demo")
    response = await client.post(
        "/api/agui",
        json=_run_input("t-owned", "me mostra a conversa"),
        headers={"Authorization": f"Bearer {bruno_token}"},
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "not accessible" in response.json()["detail"]


async def test_conversation_detail_restores_messages_and_citations(
    client: httpx.AsyncClient,
) -> None:
    token = await login(client)
    await _stream(client, token, _run_input("t-history", "Qual a taxa do consignado?"))

    response = await client.get(
        "/api/conversations/t-history", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] == "t-history"
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert "taxa do consignado" in payload["messages"][0]["content"]
    assert payload["messages"][1]["citations"] == [
        {"document_id": "tarifas", "title": "Tarifas 2026", "section": "Consignado", "page": None}
    ]


async def test_conversation_detail_hides_other_users_and_unknown_threads(
    client: httpx.AsyncClient,
) -> None:
    ana_token = await login(client, "ana@demo")
    await _stream(client, ana_token, _run_input("t-history-owned", "Qual a taxa do consignado?"))
    bruno_token = await login(client, "bruno@demo")

    other = await client.get(
        "/api/conversations/t-history-owned", headers={"Authorization": f"Bearer {bruno_token}"}
    )
    unknown = await client.get(
        "/api/conversations/does-not-exist", headers={"Authorization": f"Bearer {bruno_token}"}
    )

    assert other.status_code == unknown.status_code == 403
    assert other.json()["detail"] == unknown.json()["detail"] == "Thread is not accessible"


async def test_unknown_thread_for_another_user_is_indistinguishable_from_forbidden(
    client: httpx.AsyncClient,
) -> None:
    """A fresh thread id is claimed (200); an owned one is refused (403). The
    only way to tell them apart is to own the thread."""
    bruno_token = await login(client, "bruno@demo")
    stream = await _stream(client, bruno_token, _run_input("t-fresh", "oi"))
    assert "event: RUN_FINISHED" in stream


async def test_anonymous_requests_are_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/agui", json=_run_input("t-anon", "oi"))
    assert response.status_code == 401


async def test_conversation_survives_a_restart_and_keeps_context(
    client: httpx.AsyncClient, settings: Settings, container: Container
) -> None:
    """US-2.2: a fresh app + fresh checkpointer resumes the thread from
    Postgres, with the prior turns still in state."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    token = await login(client)
    await _stream(client, token, _run_input("t-restart", "Qual a taxa do consignado?"))

    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_dsn(settings.database_url)
    ) as checkpointer:
        deps = GraphDependencies(
            llm=_scripted_llm(),
            retrieval=StubRetrieval(grounded(evidence("1,49% a.m."))),
            authorization=StubAuthorization(),
            customer_references=DemoCustomerReferenceResolver(),
            events=RecordingEventPublisher(),
            clock=container.clock,
            id_generator=container.id_generator,
            config=GraphConfig(grounding_judge_enabled=False),
        )
        graph = build_graph(deps, checkpointer=checkpointer)
        state = await graph.aget_state({"configurable": {"thread_id": "t-restart"}})

    messages = state.values["messages"]
    assert any("taxa do consignado" in m.content for m in messages)
    assert any("1,49" in m.content for m in messages)


async def test_rate_limit_protects_the_llm_quota(
    settings: Settings, container: Container, runtime: ConversationRuntime
) -> None:
    limited = ConversationRuntime(
        providers=runtime.providers,
        graph=runtime.graph,
        rate_limiter=SlidingWindowRateLimiter(clock=container.clock, max_per_minute=1),
    )
    app = create_app(settings=settings, container=container, conversation=limited)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        token = await login(client)
        await _stream(client, token, _run_input("t-rate", "oi"))
        second = await client.post(
            "/api/agui",
            json=_run_input("t-rate", "oi de novo"),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert second.status_code == 429


async def test_empty_message_is_rejected_without_starting_a_run(
    client: httpx.AsyncClient,
) -> None:
    token = await login(client)
    response = await client.post(
        "/api/agui",
        json={"thread_id": "t-empty", "messages": [{"role": "user", "content": "   "}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_conversations_route_lists_only_own_threads(client: httpx.AsyncClient) -> None:
    ana_token = await login(client, "ana@demo")
    await _stream(client, ana_token, _run_input("t-ana-list", "oi"))
    bruno_token = await login(client, "bruno@demo")
    await _stream(client, bruno_token, _run_input("t-bruno-list", "oi"))

    response = await client.get(
        "/api/conversations", headers={"Authorization": f"Bearer {bruno_token}"}
    )
    threads = {row["thread_id"] for row in response.json()}
    assert "t-bruno-list" in threads
    assert "t-ana-list" not in threads
