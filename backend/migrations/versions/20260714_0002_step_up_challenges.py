"""step_up_challenges table (PRD-004, BR-5)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "step_up_challenges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("operation_hash", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    # Runtime needs UPDATE for attempts/used_at bookkeeping; DELETE stays denied
    # (expired challenges are data, cleanup would be an explicit later decision).
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.step_up_challenges TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.step_up_challenges FROM app_runtime")
    op.drop_table("step_up_challenges", schema="app")
