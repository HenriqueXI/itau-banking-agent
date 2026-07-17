"""Source document model: what a loader produces before chunking (rag.md §2)."""

import hashlib
from dataclasses import dataclass

from knowledge.domain.values import SourceType


@dataclass(frozen=True, kw_only=True)
class DocumentSection:
    """A logical span of a loaded document: a heading section or a PDF page.

    `heading` seeds chunk `section` metadata; `page` is set for paginated
    sources so tariff citations can point at a page.
    """

    heading: str
    text: str
    page: int | None = None


@dataclass(frozen=True, kw_only=True)
class LoadedDocument:
    """A normalized document ready to chunk.

    `content_hash` is derived from the raw text so re-ingestion of unchanged
    content is a no-op (PRD002-FR-5). Loaders build these; chunking consumes them.
    """

    document_id: str
    title: str
    source_type: SourceType
    sections: tuple[DocumentSection, ...]
    content_hash: str

    @staticmethod
    def compute_hash(raw: str) -> str:
        """Stable SHA-256 of normalized text — the idempotency key."""
        return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()
