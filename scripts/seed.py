#!/usr/bin/env python3
"""Seed demo personas (PRD004-FR-1, personas.md): ana/bruno/carla@demo.

Idempotent upserts keyed by email. Run through the backend environment so
argon2/SQLAlchemy are available:  cd backend && uv run python ../scripts/seed.py
Refuses ENV=prod (environments.md). Password comes from SEED_PASSWORD
(default demo123 — demo stack only, never a real secret).
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

async def seed(database_url: str, password: str) -> None:
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import create_async_engine

    from identity_access.adapters.outbound.postgres.tables import users
    from identity_access.adapters.outbound.security.argon2_hasher import Argon2PasswordHasher
    from shared.demo_personas import DEMO_PERSONAS

    hasher = Argon2PasswordHasher()
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for persona in DEMO_PERSONAS:
                statement = insert(users).values(
                    id=persona.user_id,
                    email=persona.email,
                    name=persona.name,
                    role=persona.role,
                    customer_id=persona.customer_id,
                    password_hash=hasher.hash(password),
                    created_at=datetime.now(UTC),
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[users.c.email],
                    set_={
                        "name": statement.excluded.name,
                        "role": statement.excluded.role,
                        "customer_id": statement.excluded.customer_id,
                        "password_hash": statement.excluded.password_hash,
                    },
                )
                await connection.execute(statement)
                print(f"seed: upserted {persona.email} ({persona.role})")
    finally:
        await engine.dispose()


def main() -> int:
    if os.environ.get("ENV") == "prod":
        print("seed: refusing to run with ENV=prod (environments.md)")
        return 1
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("seed: DATABASE_URL is required")
        return 1
    password = os.environ.get("SEED_PASSWORD", "demo123")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed(database_url, password))
    print("seed: done (idempotent — safe to run again)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
