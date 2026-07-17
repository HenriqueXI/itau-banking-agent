"""PIX transfer reservation ledger.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pix_daily_buckets",
        sa.Column("customer_id", sa.Text(), primary_key=True),
        sa.Column("local_day", sa.Date(), primary_key=True),
        sa.Column("pending_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("executed_amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_table(
        "pix_transfers",
        sa.Column("operation_hash", sa.Text(), primary_key=True),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("recipient_key", sa.Text(), nullable=False),
        sa.Column("recipient_key_masked", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("local_day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_pix_transfers_customer_id", "pix_transfers", ["customer_id"])
    op.create_index("ix_pix_transfers_local_day", "pix_transfers", ["local_day"])


def downgrade() -> None:
    op.drop_index("ix_pix_transfers_local_day", table_name="pix_transfers")
    op.drop_index("ix_pix_transfers_customer_id", table_name="pix_transfers")
    op.drop_table("pix_transfers")
    op.drop_table("pix_daily_buckets")
