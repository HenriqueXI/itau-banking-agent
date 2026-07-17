"""Admin audit API against a migrated PostgreSQL database."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.app import create_app
from audit.adapters.outbound.postgres.audit_log_repository import PostgresAuditLogRepository
from audit.domain.entities import AuditEvent
from identity_access.adapters.outbound.postgres.tables import users
from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
from identity_access.adapters.outbound.security.jwt_codec import JwtCodec
from identity_access.application.dto import TokenClaims
from identity_access.domain.values import Role
from shared.adapters.clock import SystemClock
from shared.adapters.outbox import outbox
from shared.config import Settings
from shared.container import Container

pytestmark = pytest.mark.integration

JWT_SECRET = "audit-integration-secret-0123456789abcdef"
ANA_ID = uuid.UUID("00000000-0000-0000-0000-000000000a1a")
BRUNO_ID = uuid.UUID("00000000-0000-0000-0000-000000000b2b")
CARLA_ID = uuid.UUID("00000000-0000-0000-0000-000000000c3c")


@pytest.fixture
def settings(migrated_database: dict[str, str]) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url=migrated_database["asyncpg"],
        jwt_secret=JWT_SECRET,
    )


@pytest.fixture
async def container(settings: Settings) -> Container:
    container = Container.build(settings)
    hasher = Argon2PasswordHasher()
    now = datetime.now(UTC)
    rows = [
        (ANA_ID, "ana@demo", "Ana", "customer", "123"),
        (BRUNO_ID, "bruno@demo", "Bruno", "manager", None),
        (CARLA_ID, "carla@demo", "Carla", "admin", None),
    ]
    async with container.engine.begin() as connection:
        for user_id, email, name, role, customer_id in rows:
            statement = insert(users).values(
                id=user_id,
                email=email,
                name=name,
                role=role,
                customer_id=customer_id,
                password_hash=hasher.hash("demo123"),
                created_at=now,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[users.c.email],
                    set_={
                        "role": role,
                        "customer_id": customer_id,
                        "password_hash": hasher.hash("demo123"),
                    },
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


def token(user_id: uuid.UUID, role: Role, customer_id: str | None = None) -> str:
    now = datetime.now(UTC)
    return JwtCodec(secret=JWT_SECRET, clock=SystemClock()).encode(
        TokenClaims(
            sub=str(user_id),
            role=role.value,
            customer_id=customer_id,
            iat=now,
            exp=now + timedelta(minutes=60),
            jti=f"audit-{user_id}",
        )
    )


async def append_event(
    container: Container,
    *,
    event_id: int,
    occurred_at: datetime,
    action: str = "PIX",
    user_ref: str = str(ANA_ID),
) -> AuditEvent:
    event = AuditEvent(
        id=uuid.UUID(int=event_id),
        event_id=uuid.UUID(int=event_id + 100),
        user_ref=user_ref,
        action=action,
        amount=Decimal("20.00"),
        occurred_at=occurred_at,
        resource="customer:123",
        outcome="executed",
        trace_id="trace-1",
        details={"reason": "ok"},
    )
    async with container.session_factory() as session, session.begin():
        assert await PostgresAuditLogRepository(session).append(event)
    return event


async def test_admin_lists_and_filters_audit_rows(
    client: httpx.AsyncClient, container: Container
) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await append_event(container, event_id=1, occurred_at=now)
    await append_event(container, event_id=2, occurred_at=now + timedelta(minutes=1))
    headers = {"Authorization": f"Bearer {token(CARLA_ID, Role.ADMIN)}"}

    response = await client.get(
        "/api/admin/audit",
        params={"user": "ana@demo", "action": "PIX", "page_size": 1},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == str(uuid.UUID(int=2))
    assert response.json()[0]["actor"] == {
        "id": str(ANA_ID),
        "name": "Ana",
        "email": "ana@demo",
        "role": "customer",
    }


@pytest.mark.parametrize(
    ("actor_query", "event_id"),
    [("Ana", 11), ("ana@demo", 12), (str(ANA_ID)[:18], 13)],
)
async def test_admin_filters_audit_rows_by_actor_name_email_or_uuid_prefix(
    client: httpx.AsyncClient, container: Container, actor_query: str, event_id: int
) -> None:
    await append_event(
        container, event_id=event_id, occurred_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    )
    headers = {"Authorization": f"Bearer {token(CARLA_ID, Role.ADMIN)}"}

    response = await client.get("/api/admin/audit", params={"user": actor_query}, headers=headers)

    assert response.status_code == 200
    assert int(response.headers["X-Total-Count"]) >= 1
    assert str(uuid.UUID(int=event_id)) in {item["id"] for item in response.json()}
    assert all(item["actor"]["name"] == "Ana" for item in response.json())


async def test_admin_filter_with_no_matching_actor_returns_an_empty_page(
    client: httpx.AsyncClient,
) -> None:
    headers = {"Authorization": f"Bearer {token(CARLA_ID, Role.ADMIN)}"}

    response = await client.get(
        "/api/admin/audit", params={"user": "Pessoa Inexistente"}, headers=headers
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "0"
    assert response.json() == []


async def test_admin_returns_system_for_event_without_a_human_actor(
    client: httpx.AsyncClient, container: Container
) -> None:
    await append_event(
        container,
        event_id=14,
        occurred_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        user_ref="system",
    )
    headers = {"Authorization": f"Bearer {token(CARLA_ID, Role.ADMIN)}"}

    response = await client.get(f"/api/admin/audit/{uuid.UUID(int=14)}", headers=headers)

    assert response.status_code == 200
    assert response.json()["actor"] is None


@pytest.mark.parametrize(
    ("user_id", "role", "customer_id"),
    [(ANA_ID, Role.CUSTOMER, "123"), (BRUNO_ID, Role.MANAGER, None)],
)
async def test_non_admin_is_forbidden_and_denial_enters_outbox(
    client: httpx.AsyncClient,
    container: Container,
    user_id: uuid.UUID,
    role: Role,
    customer_id: str | None,
) -> None:
    response = await client.get(
        "/api/admin/audit",
        headers={"Authorization": f"Bearer {token(user_id, role, customer_id)}"},
    )

    assert response.status_code == 403
    async with container.engine.connect() as connection:
        event_type = await connection.scalar(
            select(outbox.c.event_type)
            .where(outbox.c.actor_user_id == str(user_id))
            .order_by(outbox.c.id.desc())
            .limit(1)
        )
    assert event_type == "identity.AuthorizationDenied"


async def test_invalid_range_and_unknown_detail_are_reported(
    client: httpx.AsyncClient,
) -> None:
    headers = {"Authorization": f"Bearer {token(CARLA_ID, Role.ADMIN)}"}
    invalid = await client.get(
        "/api/admin/audit",
        params={"from": "2026-07-16T00:00:00Z", "to": "2026-07-15T00:00:00Z"},
        headers=headers,
    )
    missing = await client.get(f"/api/admin/audit/{uuid.uuid4()}", headers=headers)

    assert invalid.status_code == 422
    assert missing.status_code == 404
