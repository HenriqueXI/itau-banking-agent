"""audit_events table (database.md §1) — insert-only, immutability enforced
by trigger + grants in the initial migration (BR-7.3)."""

from sqlalchemy import Column, DateTime, Index, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from shared.adapters.database import metadata

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("user_ref", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("amount", Numeric(14, 2), nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("resource", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    # System jobs may legitimately have no Langfuse trace (PRD-009).
    Column("trace_id", Text, nullable=True),
    Column("details", JSONB, nullable=False, server_default="{}"),
    Index("ix_audit_events_user_ref_occurred_at", "user_ref", "occurred_at"),
    Index("ix_audit_events_action_occurred_at", "action", "occurred_at"),
)
