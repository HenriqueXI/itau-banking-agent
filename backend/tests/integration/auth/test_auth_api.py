"""Auth API against a real migrated PostgreSQL (PRD-004 acceptance)."""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.fakes.providers import RecordingEventPublisher, SequentialIdGenerator

from api.app import create_app
from banking.adapters.outbound.postgres.pending_operation_repository import (
    PostgresPendingOperationRepository,
)
from banking.domain.pending_operation import OperationStatus, PendingOperation
from identity_access.adapters.outbound.postgres.step_up_repository import (
    PostgresStepUpChallengeRepository,
)
from identity_access.adapters.outbound.postgres.tables import step_up_challenges, users
from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
from identity_access.adapters.outbound.security.jwt_codec import JwtCodec
from identity_access.application.dto import TokenClaims, VerifyStepUpCommand
from identity_access.application.use_cases.verify_step_up import VerifyStepUp
from identity_access.domain.values import AuthenticatedUser, Role
from shared.adapters.clock import SystemClock
from shared.config import Settings
from shared.container import Container
from shared.domain.result import is_ok

pytestmark = pytest.mark.integration

JWT_SECRET = "integration-secret-0123456789abcdef0123456789ab"
ANA_ID = uuid.UUID("00000000-0000-0000-0000-000000000a1a")
ANA_PASSWORD = "demo123"


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
    # Seed Ana directly (idempotent) — the seed script path is tested separately.
    hasher = Argon2PasswordHasher()
    async with container.engine.begin() as connection:
        statement = insert(users).values(
            id=ANA_ID,
            email="ana@demo",
            name="Ana",
            role="customer",
            customer_id="123",
            password_hash=hasher.hash(ANA_PASSWORD),
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


@pytest.fixture
async def client(settings: Settings, container: Container):
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "ana@demo", "password": ANA_PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_pending_pix(container: Container, operation_hash: str) -> None:
    now = datetime.now(UTC)
    operation = PendingOperation.create(
        operation_id=uuid.uuid5(uuid.NAMESPACE_URL, operation_hash),
        user_id=ANA_ID,
        tool="fazer_pix",
        params={"customer_id": "123", "account_id": "acc-1", "amount": "1001.00"},
        tier=3,
        now=now,
        ttl=timedelta(minutes=5),
    )
    operation = replace(
        operation,
        operation_hash=operation_hash,
        idempotency_key=f"key-{operation_hash}",
        status=OperationStatus.PENDING_STEP_UP,
    )
    async with container.session_factory() as session, session.begin():
        await PostgresPendingOperationRepository(session).add(operation)


class TestLogin:
    async def test_valid_credentials_return_verifiable_jwt(self, client: httpx.AsyncClient) -> None:
        """Acceptance: Ana's JWT carries {role: customer, customer_id: "123"}."""
        token = await login(client)
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["sub"] == str(ANA_ID)
        assert payload["role"] == "customer"
        assert payload["customer_id"] == "123"
        assert payload["exp"] - payload["iat"] == 3600
        assert payload["jti"]

    async def test_unknown_user_and_wrong_password_responses_identical(
        self, client: httpx.AsyncClient
    ) -> None:
        """Acceptance: no user-existence leak — bodies differ only in correlation_id."""
        unknown = await client.post(
            "/api/auth/login", json={"email": "ghost@demo", "password": ANA_PASSWORD}
        )
        wrong = await client.post("/api/auth/login", json={"email": "ana@demo", "password": "nope"})
        assert unknown.status_code == wrong.status_code == 401
        unknown_body, wrong_body = unknown.json(), wrong.json()
        unknown_body.pop("correlation_id")
        wrong_body.pop("correlation_id")
        assert unknown_body == wrong_body


class TestStepUpRoute:
    async def test_issues_challenge_and_persists_row(
        self, client: httpx.AsyncClient, container: Container
    ) -> None:
        token = await login(client)
        await create_pending_pix(container, "op-integration-1")
        response = await client.post(
            "/api/auth/step-up/request",
            json={"operation_hash": "op-integration-1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["delivery"] == "simulated"
        assert body["dev_code"] is not None and len(body["dev_code"]) == 6

        async with container.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(step_up_challenges).where(
                        step_up_challenges.c.id == uuid.UUID(body["challenge_id"])
                    )
                )
            ).one()
        assert row.user_id == ANA_ID
        assert row.operation_hash == "op-integration-1"
        assert body["dev_code"] not in row.code_hash  # never plaintext at rest

    async def test_expired_token_rejected(self, client: httpx.AsyncClient) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        codec = JwtCodec(secret=JWT_SECRET, clock=SystemClock())
        expired = codec.encode(
            TokenClaims(
                sub=str(ANA_ID),
                role="customer",
                customer_id="123",
                iat=past,
                exp=past + timedelta(minutes=60),
                jti="expired-jti",
            )
        )
        response = await client.post(
            "/api/auth/step-up/request",
            json={"operation_hash": "op-x"},
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401


class TestConcurrentVerify:
    async def test_row_lock_lets_exactly_one_verify_win(
        self, client: httpx.AsyncClient, container: Container
    ) -> None:
        """PRD-004 edge case: concurrent verifies serialize; single-use holds."""
        token = await login(client)
        await create_pending_pix(container, "op-concurrent")
        issued = await client.post(
            "/api/auth/step-up/request",
            json={"operation_hash": "op-concurrent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        challenge_id = uuid.UUID(issued.json()["challenge_id"])
        code = issued.json()["dev_code"]
        ana = AuthenticatedUser(id=ANA_ID, role=Role.CUSTOMER, customer_id="123")

        # Two independent engines/sessions so the row lock is real, not shared.
        async def verify() -> bool:
            engine = create_async_engine(container.settings.database_url)
            try:
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as session, session.begin():
                    use_case = VerifyStepUp(
                        challenges=PostgresStepUpChallengeRepository(session),
                        clock=SystemClock(),
                        id_generator=SequentialIdGenerator(),
                        event_publisher=RecordingEventPublisher(),
                    )
                    result = await use_case.execute(
                        VerifyStepUpCommand(
                            user=ana,
                            challenge_id=challenge_id,
                            operation_hash="op-concurrent",
                            code=code,
                        )
                    )
                    return is_ok(result)
            finally:
                await engine.dispose()

        outcomes = await asyncio.gather(verify(), verify())
        assert sorted(outcomes) == [False, True]
