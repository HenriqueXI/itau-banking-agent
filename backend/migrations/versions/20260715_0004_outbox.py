"""transactional outbox (PRD-014)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processed', 'failed')", name="outbox_status_valid"
        ),
        schema="app",
    )
    op.create_index(
        "ix_outbox_pending_dispatch",
        "outbox",
        ["status", "next_attempt_at", "id"],
        schema="app",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.outbox TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.outbox FROM app_runtime")
    op.drop_index("ix_outbox_pending_dispatch", table_name="outbox", schema="app")
    op.drop_table("outbox", schema="app")
