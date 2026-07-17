"""Initial migration: schema, tables, audit immutability, runtime-role grants."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_database: dict[str, str]):
    engine = create_async_engine(migrated_database["asyncpg"])
    yield engine
    await engine.dispose()


async def _insert_audit_row(engine: AsyncEngine) -> uuid.UUID:
    row_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO app.audit_events
                    (id, event_id, user_ref, action, occurred_at, resource, outcome, trace_id)
                VALUES
                    (:id, :event_id, 'u-1', 'PIX', :occurred_at, 'customer:123', 'executed', 'tr-1')
                """
            ),
            {"id": row_id, "event_id": uuid.uuid4(), "occurred_at": datetime.now(UTC)},
        )
    return row_id


async def test_tables_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'app' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in result}
    assert {"users", "audit_events", "step_up_challenges", "outbox", "alembic_version"} <= tables


async def test_users_role_check_constraint(engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO app.users (id, email, name, role, password_hash, created_at) "
                    "VALUES (:id, 'x@demo', 'X', 'superuser', 'h', :now)"
                ),
                {"id": uuid.uuid4(), "now": datetime.now(UTC)},
            )


async def test_audit_events_insert_allowed(engine: AsyncEngine) -> None:
    await _insert_audit_row(engine)


async def test_audit_events_update_rejected(engine: AsyncEngine) -> None:
    row_id = await _insert_audit_row(engine)
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE app.audit_events SET outcome = 'tampered' WHERE id = :id"),
                {"id": row_id},
            )


async def test_audit_events_delete_rejected(engine: AsyncEngine) -> None:
    row_id = await _insert_audit_row(engine)
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM app.audit_events WHERE id = :id"), {"id": row_id}
            )


async def test_runtime_role_grants_are_insert_select_only(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'app_runtime' "
                "AND table_schema = 'app' AND table_name = 'audit_events'"
            )
        )
        privileges = {row[0] for row in result}
    assert privileges == {"SELECT", "INSERT"}


async def test_runtime_role_cannot_delete_step_up_challenges(engine: AsyncEngine) -> None:
    """Migration 0002: SELECT/INSERT/UPDATE for bookkeeping, no DELETE."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'app_runtime' "
                "AND table_schema = 'app' AND table_name = 'step_up_challenges'"
            )
        )
        privileges = {row[0] for row in result}
    assert privileges == {"SELECT", "INSERT", "UPDATE"}


async def test_runtime_role_can_persist_and_relay_outbox_rows(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'app_runtime' "
                "AND table_schema = 'app' AND table_name = 'outbox'"
            )
        )
        privileges = {row[0] for row in result}
    assert privileges == {"SELECT", "INSERT", "UPDATE"}
