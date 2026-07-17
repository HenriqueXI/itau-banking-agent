"""conversation_threads table (PRD-006, thread ownership)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_conversation_threads_user_id", "conversation_threads", ["user_id"], schema="app"
    )
    # Ownership is claimed once and never transferred: no UPDATE grant. The
    # checkpointer manages its own tables under its own migrations.
    op.execute("GRANT SELECT, INSERT ON app.conversation_threads TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.conversation_threads FROM app_runtime")
    op.drop_index("ix_conversation_threads_user_id", "conversation_threads", schema="app")
    op.drop_table("conversation_threads", schema="app")
