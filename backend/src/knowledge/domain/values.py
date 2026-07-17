"""Knowledge value objects: chunks, metadata, citations (rag.md §1, §3)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SourceType(StrEnum):
    """Corpus source category — drives chunking policy and retrieval filters."""

    TARIFF = "tariff"
    FAQ = "faq"
    REGULATION = "regulation"


@dataclass(frozen=True, kw_only=True)
class ChunkMetadata:
    """Citation- and filter-driving metadata carried by every chunk (rag.md §2).

    `page` is set for paginated sources (PDF tariffs), `None` otherwise.
    `content_hash` is the parent document's hash — the idempotency key.
    """

    document_id: str
    title: str
    source_type: SourceType
    section: str
    chunk_index: int
    content_hash: str
    version: int
    ingested_at: datetime
    page: int | None = None

    def to_primitives(self) -> dict[str, str | int]:
        """Flat, Chroma-storable form (no None values — Chroma rejects them)."""
        data: dict[str, str | int] = {
            "document_id": self.document_id,
            "title": self.title,
            "source_type": self.source_type.value,
            "section": self.section,
            "chunk_index": self.chunk_index,
            "content_hash": self.content_hash,
            "version": self.version,
            "ingested_at": self.ingested_at.isoformat(),
        }
        if self.page is not None:
            data["page"] = self.page
        return data


@dataclass(frozen=True, kw_only=True)
class Chunk:
    """A retrievable unit: text plus the metadata that lets us cite it."""

    id: str
    text: str
    metadata: ChunkMetadata


@dataclass(frozen=True, kw_only=True)
class Citation:
    """Structured citation rendered as `【title — section/page】` (rag.md §4)."""

    document_id: str
    title: str
    section: str
    page: int | None = None

    def marker(self) -> str:
        locus = f"p.{self.page}" if self.page is not None else self.section
        return f"【{self.title} — {locus}】"


@dataclass(frozen=True, kw_only=True)
class ScoredChunk:
    """A retrieved chunk with its relevance score (higher = more relevant)."""

    chunk: Chunk
    score: float

    def citation(self) -> Citation:
        m = self.chunk.metadata
        return Citation(document_id=m.document_id, title=m.title, section=m.section, page=m.page)
