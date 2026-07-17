"""Alembic environment — async engine, URL from DATABASE_URL only."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Register every module's tables on the shared metadata.
from audit.adapters.outbound.postgres import tables as audit_tables  # noqa: F401
from identity_access.adapters.outbound.postgres import tables as identity_tables  # noqa: F401
from shared.adapters import outbox as outbox_tables  # noqa: F401
from shared.adapters.database import metadata

config = context.config

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is required to run migrations")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = metadata


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema="app",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS app")
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
        await connection.commit()
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
