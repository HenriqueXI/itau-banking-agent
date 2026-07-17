"""conversation_threads table (database.md §1).

Thread ownership only. Conversation *content* lives in the LangGraph
checkpointer's own tables (`langgraph-checkpoint-postgres` owns that schema) —
duplicating messages here would give us two sources of truth for one turn.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from shared.adapters.database import metadata

conversation_threads = Table(
    "conversation_threads",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
