"""Knowledge domain events.

`DocumentIngested` is persisted through the transactional outbox. The audit
consumer is attached in PRD-009.
"""

from dataclasses import dataclass
from typing import ClassVar

from shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class DocumentIngested(DomainEvent):
    event_type: ClassVar[str] = "knowledge.DocumentIngested"

    document_id: str
    title: str
    source_type: str
    document_version: int
    chunk_count: int
    content_hash: str
