"""Tables owned by the banking module."""

from sqlalchemy import Column, Date, DateTime, Integer, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from shared.adapters.database import metadata

pending_operations = Table(
    "pending_operations",
    metadata,
    Column("operation_id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("tool", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("tier", Integer, nullable=False),
    Column("operation_hash", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("cancellation_reason", Text, nullable=True),
    Column("idempotency_key", Text, nullable=True, unique=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
)

pix_daily_buckets = Table(
    "pix_daily_buckets",
    metadata,
    Column("customer_id", Text, primary_key=True),
    Column("local_day", Date, primary_key=True),
    Column("pending_amount", Numeric(14, 2), nullable=False),
    Column("executed_amount", Numeric(14, 2), nullable=False),
)

pix_transfers = Table(
    "pix_transfers",
    metadata,
    Column("operation_hash", Text, primary_key=True),
    Column("customer_id", Text, nullable=False, index=True),
    Column("account_id", Text, nullable=False),
    Column("recipient_key", Text, nullable=False),
    Column("recipient_key_masked", Text, nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
    Column("local_day", Date, nullable=False, index=True),
    Column("status", Text, nullable=False),
    Column("receipt", JSONB, nullable=True),
)
