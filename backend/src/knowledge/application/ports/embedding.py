"""EmbeddingPort: turn text into vectors (Gemini primary, Ollama local — ADR-008)."""

from typing import Protocol


class EmbeddingError(Exception):
    """Embedding provider unreachable/failed — mapped to KnowledgeUnavailable."""


class EmbeddingPort(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed corpus chunks (task-type: retrieval document). Raises EmbeddingError."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query (task-type: retrieval query). Raises EmbeddingError."""
        ...

    def dimension(self) -> int:
        """Vector dimension this model produces (guards collection compatibility)."""
        ...
