"""Persist the simulated core-banking read model owned by the MCP server.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    money = sa.Numeric(18, 2)
    op.create_table(
        "core_accounts",
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column("available_balance", money, nullable=False),
        sa.PrimaryKeyConstraint("customer_id", "account_id"),
        schema="app",
    )
    op.create_table(
        "core_cards",
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("card_id", sa.String(64), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("current_limit", money, nullable=False),
        sa.Column("used_amount", money, nullable=False),
        sa.Column("invoice_amount", money, nullable=False),
        sa.Column("invoice_due_day", sa.SmallInteger(), nullable=False),
        sa.Column("invoice_status", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("customer_id", "card_id"),
        schema="app",
    )
    op.create_table(
        "core_transactions",
        sa.Column("transaction_id", sa.String(96), primary_key=True),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("description", sa.String(256), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_core_transactions_customer_account_occurred",
        "core_transactions",
        ["customer_id", "account_id", "occurred_at"],
        schema="app",
    )
    op.create_table(
        "core_limit_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("card_id", sa.String(64), nullable=False),
        sa.Column("old_limit", money, nullable=False),
        sa.Column("new_limit", money, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_table(
        "core_pix_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("transaction_id", sa.String(96), nullable=False, unique=True),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("recipient_key_masked", sa.String(256), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("e2e_id", sa.String(64), nullable=False),
        schema="app",
    )

    op.execute(
        """
        INSERT INTO app.core_accounts (customer_id, account_id, account_type, available_balance)
        VALUES
          ('123', 'acc-1', 'checking', 28412.37),
          ('456', 'acc-2', 'checking', 23980.15),
          ('789', 'acc-3', 'checking', 5120.44)
        """
    )
    op.execute(
        """
        INSERT INTO app.core_cards
          (customer_id, card_id, last4, current_limit, used_amount, invoice_amount,
           invoice_due_day, invoice_status, updated_at)
        VALUES
          ('123', 'card-1', '4242', 5000.00, 1834.90, 1834.90, 10, 'OPEN', CURRENT_TIMESTAMP),
          ('123', 'card-2', '8888', 3000.00, 420.00, 420.00, 20, 'OPEN', CURRENT_TIMESTAMP),
          ('456', 'card-3', '8801', 25000.00, 6420.30, 6420.30, 15, 'OPEN', CURRENT_TIMESTAMP),
          ('789', 'card-4', '1177', 8000.00, 920.15, 920.15, 5, 'OPEN', CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        INSERT INTO app.core_transactions
          (transaction_id, customer_id, account_id, description, amount, kind, occurred_at)
        VALUES
          ('t1', '123', 'acc-1', 'Supermercado Pão de Açúcar',
           -284.52, 'debit', CURRENT_TIMESTAMP - INTERVAL '2 days'),
          ('t2', '123', 'acc-1', 'PIX recebido — Maria S.',
           350.00, 'credit', CURRENT_TIMESTAMP - INTERVAL '1 day'),
          ('t3', '123', 'acc-1', 'Restaurante Coco Bambu',
           -187.40, 'debit', CURRENT_TIMESTAMP - INTERVAL '6 hours')
        """
    )


def downgrade() -> None:
    op.drop_table("core_pix_receipts", schema="app")
    op.drop_table("core_limit_receipts", schema="app")
    op.drop_index(
        "ix_core_transactions_customer_account_occurred",
        "core_transactions",
        schema="app",
    )
    op.drop_table("core_transactions", schema="app")
    op.drop_table("core_cards", schema="app")
    op.drop_table("core_accounts", schema="app")
