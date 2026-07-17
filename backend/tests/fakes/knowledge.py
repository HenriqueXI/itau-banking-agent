"""Deterministic, offline knowledge fakes (NFR-7).

`LexicalEmbedder` maps text to a hashed bag-of-words vector so cosine similarity
tracks lexical overlap — no network, fully reproducible. `InMemoryVectorStore`
implements VectorStorePort with cosine ranking. Together they let unit tests and
the deterministic eval harness exercise chunking + retrieval + the relevance
floor without Chroma or a real embedding provider.
"""

import math
import re
import unicodedata
import zlib

from knowledge.application.ports.vector_store import StoredDocumentState
from knowledge.domain.values import Chunk, ScoredChunk, SourceType

_WORD = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> list[str]:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return _WORD.findall(stripped)


class LexicalEmbedder:
    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension

    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for word in _normalize(text):
            # crc32, not hash(): str hashing is salted per process (PYTHONHASHSEED).
            vector[zlib.crc32(word.encode()) % self._dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: list[tuple[Chunk, list[float]]] = []

    async def collection_dimension(self) -> int | None:
        return len(self._rows[0][1]) if self._rows else None

    async def document_state(self, document_id: str) -> StoredDocumentState | None:
        for chunk, _ in self._rows:
            if chunk.metadata.document_id == document_id:
                return StoredDocumentState(
                    content_hash=chunk.metadata.content_hash, version=chunk.metadata.version
                )
        return None

    async def list_document_ids(self) -> set[str]:
        return {chunk.metadata.document_id for chunk, _ in self._rows}

    async def replace_document(
        self, document_id: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        self._rows = [r for r in self._rows if r[0].metadata.document_id != document_id]
        self._rows.extend(zip(chunks, embeddings, strict=True))

    async def delete_document(self, document_id: str) -> None:
        self._rows = [r for r in self._rows if r[0].metadata.document_id != document_id]

    async def query(
        self, embedding: list[float], *, top_k: int, source_type: SourceType | None
    ) -> list[ScoredChunk]:
        scored = [
            ScoredChunk(chunk=chunk, score=_cosine(embedding, vector))
            for chunk, vector in self._rows
            if source_type is None or chunk.metadata.source_type is source_type
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]
