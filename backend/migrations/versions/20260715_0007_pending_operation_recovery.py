"""Persist idempotency and resolution data for confirmation workflows.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pending_operations",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        schema="app",
    )
    op.add_column(
        "pending_operations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    op.create_unique_constraint(
        "uq_pending_operations_idempotency_key",
        "pending_operations",
        ["idempotency_key"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint("uq_pending_operations_idempotency_key", "pending_operations", schema="app")
    op.drop_column("pending_operations", "resolved_at", schema="app")
    op.drop_column("pending_operations", "idempotency_key", schema="app")
