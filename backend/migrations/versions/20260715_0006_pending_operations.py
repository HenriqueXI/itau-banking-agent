"""pending operation state for confirmation-gated banking workflows

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_operations",
        sa.Column("operation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("operation_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'executing', 'executed', "
            "'cancelled', 'expired', 'failed')",
            name="pending_operation_status_valid",
        ),
        schema="app",
    )
    op.create_index(
        "ix_pending_operations_user_id", "pending_operations", ["user_id"], schema="app"
    )
    op.create_index(
        "ix_pending_operations_expires_at", "pending_operations", ["expires_at"], schema="app"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.pending_operations TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.pending_operations FROM app_runtime")
    op.drop_index("ix_pending_operations_expires_at", "pending_operations", schema="app")
    op.drop_index("ix_pending_operations_user_id", "pending_operations", schema="app")
    op.drop_table("pending_operations", schema="app")
