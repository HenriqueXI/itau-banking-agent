"""initial: schema app, users, audit_events, runtime role + immutability

Revision ID: 0001
Revises:
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('customer', 'manager', 'admin')", name="role_valid"),
        sa.CheckConstraint(
            "role != 'customer' OR customer_id IS NOT NULL",
            name="customer_requires_customer_id",
        ),
        schema="app",
    )

    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_ref", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("details", JSONB(), nullable=False, server_default="{}"),
        schema="app",
    )
    op.create_index(
        "ix_audit_events_user_ref_occurred_at",
        "audit_events",
        ["user_ref", "occurred_at"],
        schema="app",
    )
    op.create_index(
        "ix_audit_events_action_occurred_at",
        "audit_events",
        ["action", "occurred_at"],
        schema="app",
    )

    # Immutability (BR-7.3), belt and suspenders:
    # 1. Trigger blocks UPDATE/DELETE for every role, including the table owner
    #    (the demo stack connects as the owner, so grants alone would not bind).
    op.execute(
        """
        CREATE FUNCTION app.forbid_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (BR-7.3)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON app.audit_events
        FOR EACH ROW EXECUTE FUNCTION app.forbid_audit_mutation()
        """
    )

    # 2. Dedicated runtime role carries the documented grant posture:
    #    INSERT/SELECT only on audit_events, no UPDATE/DELETE anywhere on it.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
                CREATE ROLE app_runtime NOLOGIN;
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA app TO app_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.users TO app_runtime")
    op.execute("GRANT SELECT, INSERT ON app.audit_events TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.audit_events FROM app_runtime")
    op.execute("REVOKE ALL ON app.users FROM app_runtime")
    op.execute("REVOKE USAGE ON SCHEMA app FROM app_runtime")
    # app_runtime role is cluster-wide and may be shared; deliberately kept.
    op.execute("DROP TRIGGER audit_events_immutable ON app.audit_events")
    op.execute("DROP FUNCTION app.forbid_audit_mutation()")
    op.drop_index("ix_audit_events_action_occurred_at", "audit_events", schema="app")
    op.drop_index("ix_audit_events_user_ref_occurred_at", "audit_events", schema="app")
    op.drop_table("audit_events", schema="app")
    op.drop_table("users", schema="app")
