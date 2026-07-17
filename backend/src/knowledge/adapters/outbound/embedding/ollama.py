"""Ollama embedding adapter (ADR-008 local/offline). No query/document asymmetry."""

import httpx

from knowledge.application.ports.embedding import EmbeddingError, EmbeddingPort

# nomic-embed-text output dimension.
_DIMENSION = 768


class OllamaEmbedder(EmbeddingPort):
    def __init__(self, *, base_url: str, model: str, dimension: int = _DIMENSION) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension

    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self._embed_one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed_one(text)

    async def _embed_one(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self._model, "prompt": text},
                )
                response.raise_for_status()
                return list(response.json()["embedding"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"ollama embedding failed: {exc}") from exc
