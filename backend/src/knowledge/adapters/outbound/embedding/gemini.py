"""Gemini embedding adapter (ADR-008 primary). Uses the async google-genai client.

Task types differ for corpus vs query (asymmetric retrieval embeddings), which
measurably improves recall. Any client/transport failure becomes EmbeddingError
so the use case degrades to KnowledgeUnavailable rather than answering from weights.

`dimension` is requested explicitly (`output_dimensionality`): gemini-embedding-001
emits 3072 by default, and a silent mismatch with what `dimension()` reports would
defeat the ingest dimension guard. Sub-3072 sizes are Matryoshka truncations and
are not unit-norm, so we re-normalize — cosine is scale-invariant, but the stored
vectors should still be what the rest of the system assumes.
"""

import math

from knowledge.application.ports.embedding import EmbeddingError, EmbeddingPort

_NATIVE_DIMENSION = 3072


class GeminiEmbedder(EmbeddingPort):
    def __init__(self, *, api_key: str, model: str, dimension: int) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._dimension = dimension

    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    async def _embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        if not texts:
            return []
        from google.genai import types

        try:
            response = await self._client.aio.models.embed_content(
                model=self._model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type, output_dimensionality=self._dimension
                ),
            )
        except Exception as exc:
            raise EmbeddingError(f"gemini embedding failed: {exc}") from exc

        # A 200 with missing vectors is still a failure: never let a silently empty
        # embedding reach the store, or retrieval would rank against a null vector.
        if not response.embeddings or len(response.embeddings) != len(texts):
            returned = len(response.embeddings or [])
            raise EmbeddingError(f"gemini returned {returned} embeddings for {len(texts)} texts")

        vectors: list[list[float]] = []
        for embedding in response.embeddings:
            if not embedding.values:
                raise EmbeddingError("gemini returned an embedding with no values")
            if len(embedding.values) != self._dimension:
                raise EmbeddingError(
                    f"gemini returned {len(embedding.values)} dims, expected {self._dimension}"
                )
            vectors.append(self._normalize(list(embedding.values)))
        return vectors

    def _normalize(self, vector: list[float]) -> list[float]:
        if self._dimension == _NATIVE_DIMENSION:
            return vector  # native output is already unit-norm
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector
