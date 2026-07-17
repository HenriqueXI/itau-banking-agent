"""users and step_up_challenges tables (database.md §1)."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from shared.adapters.database import metadata

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("customer_id", Text, nullable=True),
    Column("password_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("role IN ('customer', 'manager', 'admin')", name="role_valid"),
    CheckConstraint(
        "role != 'customer' OR customer_id IS NOT NULL", name="customer_requires_customer_id"
    ),
)

step_up_challenges = Table(
    "step_up_challenges",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("operation_hash", Text, nullable=False),
    Column("code_hash", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("used_at", DateTime(timezone=True), nullable=True),
)
