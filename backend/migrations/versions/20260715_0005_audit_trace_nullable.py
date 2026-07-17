"""allow audit rows without a trace for system-originated events

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("audit_events", "trace_id", nullable=True, schema="app")


def downgrade() -> None:
    op.execute("UPDATE app.audit_events SET trace_id = 'legacy-untraced' WHERE trace_id IS NULL")
    op.alter_column(
        "audit_events", "trace_id", existing_type=sa.Text(), nullable=False, schema="app"
    )
