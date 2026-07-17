"""Shared PostgreSQL container for integration tests (testcontainers)."""

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def database_urls(postgres_container: PostgresContainer) -> dict[str, str]:
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    credentials = (
        f"{postgres_container.username}:{postgres_container.password}"
        f"@{host}:{port}/{postgres_container.dbname}"
    )
    return {
        "asyncpg": f"postgresql+asyncpg://{credentials}",
        "psycopg": f"postgresql://{credentials}",
    }


@pytest.fixture(scope="session")
def migrated_database(database_urls: dict[str, str]) -> dict[str, str]:
    """Runs `alembic upgrade head` once against the container."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={"DATABASE_URL": database_urls["asyncpg"], **_inherited_env()},
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return database_urls


def _inherited_env() -> dict[str, str]:
    import os

    return {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
