"""ChromaDB VectorStorePort adapter (ADR-005), client/server (async) mode.

Cosine space; we supply embeddings (no server-side embedding function), so the
adapter is embedding-provider agnostic. Chroma returns cosine *distance*; we map
score = 1 - distance into [0,1] (higher = more relevant) at this boundary so the
domain never sees distances. Connection/transport failures raise VectorStoreError,
which the use cases translate to KnowledgeUnavailable (rag.md §7).
"""

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from knowledge.application.ports.vector_store import (
    StoredDocumentState,
    VectorStoreError,
    VectorStorePort,
)
from knowledge.domain.values import Chunk, ChunkMetadata, ScoredChunk, SourceType

_COSINE = {"hnsw:space": "cosine"}


def _metadata_from_primitives(meta: dict) -> ChunkMetadata:
    page = meta.get("page")
    return ChunkMetadata(
        document_id=str(meta["document_id"]),
        title=str(meta["title"]),
        source_type=SourceType(str(meta["source_type"])),
        section=str(meta["section"]),
        chunk_index=int(meta["chunk_index"]),
        content_hash=str(meta["content_hash"]),
        version=int(meta["version"]),
        ingested_at=datetime.fromisoformat(str(meta["ingested_at"])),
        page=int(page) if page is not None else None,
    )


class ChromaVectorStore(VectorStorePort):
    def __init__(self, *, url: str, collection: str) -> None:
        parsed = urlparse(url)
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or 8000
        self._ssl = parsed.scheme == "https"
        self._collection_name = collection
        # Connected lazily: constructing the store must not require a live server.
        self._collection: Any | None = None

    async def _get_collection(self) -> Any:
        if self._collection is None:
            try:
                import chromadb

                client = await chromadb.AsyncHttpClient(
                    host=self._host, port=self._port, ssl=self._ssl
                )
                self._collection = await client.get_or_create_collection(
                    name=self._collection_name, metadata=_COSINE
                )
            except Exception as exc:
                raise VectorStoreError(f"chroma connect failed: {exc}") from exc
        return self._collection

    async def collection_dimension(self) -> int | None:
        collection = await self._get_collection()
        try:
            peek = await collection.get(limit=1, include=["embeddings"])
        except Exception as exc:
            raise VectorStoreError(f"chroma peek failed: {exc}") from exc
        embeddings = peek.get("embeddings")
        if embeddings is not None and len(embeddings) > 0:
            return len(embeddings[0])
        return None

    async def document_state(self, document_id: str) -> StoredDocumentState | None:
        collection = await self._get_collection()
        try:
            result = await collection.get(
                where={"document_id": document_id}, limit=1, include=["metadatas"]
            )
        except Exception as exc:
            raise VectorStoreError(f"chroma get failed: {exc}") from exc
        metadatas = result.get("metadatas") or []
        if not metadatas:
            return None
        meta = metadatas[0]
        return StoredDocumentState(
            content_hash=str(meta["content_hash"]), version=int(meta["version"])
        )

    async def list_document_ids(self) -> set[str]:
        collection = await self._get_collection()
        try:
            result = await collection.get(include=["metadatas"])
        except Exception as exc:
            raise VectorStoreError(f"chroma list failed: {exc}") from exc
        return {str(m["document_id"]) for m in (result.get("metadatas") or [])}

    async def replace_document(
        self, document_id: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        collection = await self._get_collection()
        try:
            await collection.delete(where={"document_id": document_id})
            if not chunks:
                return
            await collection.add(
                ids=[c.id for c in chunks],
                embeddings=embeddings,
                documents=[c.text for c in chunks],
                metadatas=[c.metadata.to_primitives() for c in chunks],
            )
        except Exception as exc:
            raise VectorStoreError(f"chroma replace failed: {exc}") from exc

    async def delete_document(self, document_id: str) -> None:
        collection = await self._get_collection()
        try:
            await collection.delete(where={"document_id": document_id})
        except Exception as exc:
            raise VectorStoreError(f"chroma delete failed: {exc}") from exc

    async def query(
        self, embedding: list[float], *, top_k: int, source_type: SourceType | None
    ) -> list[ScoredChunk]:
        collection = await self._get_collection()
        where = {"source_type": source_type.value} if source_type else None
        try:
            result = await collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError(f"chroma query failed: {exc}") from exc

        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        scored: list[ScoredChunk] = []
        for chunk_id, text, meta, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            chunk = Chunk(id=chunk_id, text=text, metadata=_metadata_from_primitives(meta))
            scored.append(ScoredChunk(chunk=chunk, score=max(0.0, 1.0 - float(distance))))
        return scored
