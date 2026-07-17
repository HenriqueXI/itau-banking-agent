"""Allow the tier-3 step-up state in pending operations.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Revision 0006 used the metadata naming convention, so an installation
    # can have either the expanded physical name or the logical name.
    op.execute(
        "ALTER TABLE app.pending_operations "
        "DROP CONSTRAINT IF EXISTS ck_pending_operations_pending_operation_status_valid"
    )
    op.execute(
        "ALTER TABLE app.pending_operations "
        "DROP CONSTRAINT IF EXISTS pending_operation_status_valid"
    )
    op.execute(
        "ALTER TABLE app.pending_operations "
        "ADD CONSTRAINT pending_operation_status_valid "
        "CHECK (status IN ('pending_stepup', 'pending_confirmation', 'executing', "
        "'executed', 'cancelled', 'expired', 'failed'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.pending_operations DROP CONSTRAINT pending_operation_status_valid")
    op.execute(
        "ALTER TABLE app.pending_operations "
        "ADD CONSTRAINT pending_operation_status_valid "
        "CHECK (status IN ('pending_confirmation', 'executing', 'executed', "
        "'cancelled', 'expired', 'failed'))"
    )
