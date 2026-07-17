"""scripts/seed.py: personas land, upserts are idempotent (PRD004-FR-1)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from identity_access.adapters.outbound.postgres.tables import users

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[3]
SEED_SCRIPT = BACKEND_DIR.parent / "scripts" / "seed.py"


def run_seed(database_url: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "ENV")}
    # The test exercises the seed script, not a particular Python launcher.
    # Use uv when it is available (as in CI), but keep the integration suite
    # runnable from an already activated virtual environment on Windows.
    uv = shutil.which("uv")
    command = [uv, "run", "python", str(SEED_SCRIPT)] if uv else [sys.executable, str(SEED_SCRIPT)]
    return subprocess.run(
        command,
        cwd=BACKEND_DIR,
        env={**env, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=180,
    )


async def test_seed_is_idempotent_and_creates_personas(migrated_database: dict[str, str]) -> None:
    url = migrated_database["asyncpg"]
    first = run_seed(url)
    assert first.returncode == 0, first.stderr
    second = run_seed(url)
    assert second.returncode == 0, second.stderr

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(select(users).order_by(users.c.email))).all()
            emails = [row.email for row in rows]
            assert {"ana@demo", "bruno@demo", "carla@demo"} <= set(emails)
            total = (
                await connection.execute(
                    select(func.count())
                    .select_from(users)
                    .where(users.c.email.in_(["ana@demo", "bruno@demo", "carla@demo"]))
                )
            ).scalar_one()
            assert total == 3  # second run updated, not duplicated

            ana = next(row for row in rows if row.email == "ana@demo")
            assert ana.role == "customer"
            assert ana.customer_id == "123"
            assert "demo123" not in ana.password_hash  # argon2, never plaintext
    finally:
        await engine.dispose()


def test_seed_refuses_prod(migrated_database: dict[str, str]) -> None:
    env = {k: v for k, v in os.environ.items() if k != "ENV"}
    result = subprocess.run(
        [sys.executable, str(SEED_SCRIPT)],
        env={**env, "ENV": "prod", "DATABASE_URL": migrated_database["asyncpg"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "refusing" in result.stdout
