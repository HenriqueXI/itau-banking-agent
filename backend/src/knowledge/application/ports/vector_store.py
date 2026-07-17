"""VectorStorePort: Chroma-backed persistence for chunks + citation metadata."""

from dataclasses import dataclass
from typing import Protocol

from knowledge.domain.values import Chunk, ScoredChunk, SourceType


class VectorStoreError(Exception):
    """Vector store unreachable/failed — mapped to KnowledgeUnavailable."""


@dataclass(frozen=True, kw_only=True)
class StoredDocumentState:
    content_hash: str
    version: int


class VectorStorePort(Protocol):
    async def collection_dimension(self) -> int | None:
        """Embedding dimension of the existing collection, or None if empty."""
        ...

    async def document_state(self, document_id: str) -> StoredDocumentState | None:
        """Stored hash + version of a document, or None if absent (idempotency)."""
        ...

    async def list_document_ids(self) -> set[str]:
        """All document ids currently present (for stale-document pruning)."""
        ...

    async def replace_document(
        self, document_id: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        """Atomically drop prior versions of a document and upsert the new chunks."""
        ...

    async def delete_document(self, document_id: str) -> None:
        """Remove all chunks of a document (stale prune)."""
        ...

    async def query(
        self, embedding: list[float], *, top_k: int, source_type: SourceType | None
    ) -> list[ScoredChunk]:
        """Nearest chunks by cosine similarity, scored in [0,1] (higher = closer)."""
        ...
