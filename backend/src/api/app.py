"""FastAPI application factory. Lifespan owns the composition root."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.banking_wiring import BankingProviders, BankingWorkflowAdapter, LlmConfirmationClassifier
from api.conversation_wiring import ConversationProviders, IdentityAuthorizationAdapter, build_llm
from api.event_wiring import build_event_bus
from api.identity_wiring import IdentityProviders
from api.knowledge_wiring import KnowledgeProviders
from api.problem import register_problem_handler
from api.rate_limit import SlidingWindowRateLimiter
from api.routers.agui import router as agui_router
from api.routers.audit import router as audit_router
from api.routers.auth import router as auth_router
from api.routers.banking import router as banking_router
from api.routers.health import router as health_router
from conversation.application.prompts.library import assert_prompts_loadable
from conversation.domain.tools import assert_registry_complete
from identity_access.application.use_cases.authorize_action import AuthorizeAction
from identity_access.domain.authorization import AuthorizationService, assert_matrix_complete
from shared.adapters.event_publisher import PostgresEventPublisher, event_transaction
from shared.adapters.outbox_relay import OutboxRelay
from shared.config import Settings
from shared.container import Container
from shared.logging.setup import configure_logging

logger = structlog.get_logger(__name__)

DB_STARTUP_ATTEMPTS = 10
DB_STARTUP_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class ConversationRuntime:
    """The compiled agent + its per-user rate limiter.

    The graph is compiled once at startup with the checkpointer bound
    (backend.md runtime shape) — compiling per request would rebuild the whole
    topology on every message and drop the connection pool behind the saver.
    """

    providers: ConversationProviders
    graph: Any
    rate_limiter: SlidingWindowRateLimiter


async def wait_for_database(engine: AsyncEngine, attempts: int, delay_seconds: float) -> None:
    """Bounded retries, then crash with a clear error — no zombie app (PRD-001)."""
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return
        except Exception as exc:
            logger.warning(
                "startup.database_not_ready",
                attempt=attempt,
                max_attempts=attempts,
                error=str(exc),
            )
            if attempt == attempts:
                raise RuntimeError(
                    f"Database unreachable after {attempts} attempts — refusing to start"
                ) from exc
            await asyncio.sleep(delay_seconds)


def checkpointer_dsn(database_url: str) -> str:
    """The app speaks asyncpg, the checkpointer speaks psycopg — same database,
    different driver (`langgraph-checkpoint-postgres` ships a psycopg saver)."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: Container = app.state.container
    await wait_for_database(container.engine, DB_STARTUP_ATTEMPTS, DB_STARTUP_DELAY_SECONDS)
    settings = container.settings
    relay_stop = asyncio.Event()
    relay_task: asyncio.Task[None] | None = None
    sweeper_stop = asyncio.Event()
    sweeper_task: asyncio.Task[None] | None = None
    if settings.env != "test":
        relay = OutboxRelay(
            session_factory=container.session_factory,
            event_bus=app.state.event_bus,
            clock=container.clock,
            batch_size=settings.outbox_relay_batch_size,
            max_attempts=settings.outbox_max_attempts,
            max_backoff_seconds=settings.outbox_max_backoff_seconds,
        )
        relay_task = asyncio.create_task(
            relay.run(stop=relay_stop, interval_seconds=settings.outbox_relay_interval_seconds),
            name="outbox-relay",
        )
        sweeper_task = asyncio.create_task(
            _run_operation_sweeper(app, sweeper_stop),
            name="pending-operation-sweeper",
        )

    if getattr(app.state, "conversation", None) is not None:
        # Tests inject a runtime with fakes; don't reach for Postgres/LLMs.
        logger.info("startup.ready", env=settings.env, conversation="injected")
        yield
    else:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(
            checkpointer_dsn(settings.database_url)
        ) as checkpointer:
            await checkpointer.setup()
            app.state.conversation = build_conversation(app, checkpointer)
            logger.info("startup.ready", env=settings.env)
            yield

    if relay_task is not None:
        relay_stop.set()
        await relay_task
    if sweeper_task is not None:
        sweeper_stop.set()
        await sweeper_task
    await app.state.banking.aclose()
    _flush_traces(app)
    await container.aclose()
    logger.info("shutdown.complete")


async def _run_operation_sweeper(app: FastAPI, stop: asyncio.Event) -> None:
    """Lifecycle task kept separate from the outbox relay (PRD-007 choice)."""
    from banking.adapters.outbound.postgres.pending_operation_repository import (
        PostgresPendingOperationRepository,
        PostgresPixTransferRepository,
    )
    from banking.application.use_cases.expire_pending_operations import (
        ExpirePendingOperationsUseCase,
    )

    container: Container = app.state.container
    interval = container.settings.outbox_relay_interval_seconds
    while not stop.is_set():
        try:
            async with (
                container.session_factory() as session,
                session.begin(),
                event_transaction(session),
            ):
                await ExpirePendingOperationsUseCase(
                    operations=PostgresPendingOperationRepository(session),
                    transfers=PostgresPixTransferRepository(session),
                    events=PostgresEventPublisher(),
                    clock=container.clock,
                    id_generator=container.id_generator,
                ).execute()
        except Exception:
            logger.exception("pending_operation_sweeper_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def _flush_traces(app: FastAPI) -> None:
    """Drain the tracer's queue on the way out — traces are shipped in the
    background, so without this the last turns of a run die with the process.
    Shutdown-only: the request path never waits on Langfuse."""
    conversation = getattr(app.state, "conversation", None)
    if conversation is None:
        return
    conversation.providers.tracer.flush()


def build_conversation(app: FastAPI, checkpointer: Any) -> ConversationRuntime:
    container: Container = app.state.container
    settings = container.settings
    # Built here, not in create_app: provider clients validate their keys on
    # construction, and a test that never talks to an LLM shouldn't need one.
    knowledge: KnowledgeProviders = app.state.knowledge or KnowledgeProviders.build(settings)
    app.state.knowledge = knowledge
    # One LLM chain for both consumers: the graph nodes and the confirmation
    # classifier share failover state and telemetry wrapping.
    llm = build_llm(settings, container.clock)
    authorization = IdentityAuthorizationAdapter(
        AuthorizeAction(
            service=AuthorizationService(),
            clock=container.clock,
            id_generator=container.id_generator,
            event_publisher=PostgresEventPublisher(),
        )
    )
    providers = ConversationProviders.build(
        settings,
        container.clock,
        BankingWorkflowAdapter(
            client=app.state.banking.client,
            settings=settings,
            clock=container.clock,
            id_generator=container.id_generator,
            authorization=authorization,
            confirmation_classifier=LlmConfirmationClassifier(llm),
        ),
        llm=llm,
    )
    deps = providers.graph_dependencies(
        settings=settings,
        retrieve_use_case=knowledge.retrieve_use_case(settings),
        clock=container.clock,
        id_generator=container.id_generator,
        authorization=authorization,
    )
    return ConversationRuntime(
        providers=providers,
        graph=providers.build_graph(deps, checkpointer),
        rate_limiter=SlidingWindowRateLimiter(
            clock=container.clock, max_per_minute=settings.rate_limit_per_minute
        ),
    )


def create_app(
    settings: Settings | None = None,
    container: Container | None = None,
    identity: IdentityProviders | None = None,
    banking: BankingProviders | None = None,
    knowledge: KnowledgeProviders | None = None,
    conversation: ConversationRuntime | None = None,
) -> FastAPI:
    """Build the app. Tests pass a container/providers with fakes."""
    settings = settings or Settings()  # fails loudly on missing required env vars
    configure_logging(settings.log_level)
    # Boot-time completeness guards: a gap in any of them is a startup failure,
    # never a runtime guess (agents' prompts are files).
    assert_matrix_complete()
    assert_registry_complete()
    assert_prompts_loadable()

    container = container or Container.build(settings)
    identity = identity or IdentityProviders.build(settings, container.clock)
    banking = banking or BankingProviders.build(settings)

    app = FastAPI(
        title="Itaú Banking Agent",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.event_bus = build_event_bus(
        session_factory=container.session_factory, id_generator=container.id_generator
    )
    app.state.identity = identity
    app.state.banking = banking
    app.state.knowledge = knowledge
    app.state.conversation = conversation
    register_problem_handler(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(banking_router)
    app.include_router(agui_router)
    return app
