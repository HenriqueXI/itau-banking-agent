"""Idempotent KB ingestion (PRD002-FR-5).

Per file: load → hash-check (skip if unchanged) → chunk → embed → replace prior
version. One unparseable file is reported and skipped; the batch continues.
A DocumentIngested event is persisted per ingested document through the active
unit of work. Embedding-dimension mismatch against a non-empty collection aborts
before any writes (edge case: provider switch).
"""

import structlog

from knowledge.application.dto import (
    DocumentIngestReport,
    IngestCommand,
    IngestReport,
    SourceFile,
)
from knowledge.application.ports.document_loader import DocumentLoaderPort, DocumentLoadError
from knowledge.application.ports.embedding import EmbeddingError, EmbeddingPort
from knowledge.application.ports.vector_store import VectorStoreError, VectorStorePort
from knowledge.domain.chunking import chunk_document
from knowledge.domain.errors import embedding_dimension_mismatch, knowledge_unavailable
from knowledge.domain.events import DocumentIngested
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result

logger = structlog.get_logger(__name__)


class IngestKnowledgeBase:
    def __init__(
        self,
        *,
        loader: DocumentLoaderPort,
        embedder: EmbeddingPort,
        store: VectorStorePort,
        events: EventPublisher,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._loader = loader
        self._embedder = embedder
        self._store = store
        self._events = events
        self._clock = clock
        self._ids = id_generator

    async def execute(self, command: IngestCommand) -> Result[IngestReport, DomainError]:
        try:
            collection_dim = await self._store.collection_dimension()
        except VectorStoreError:
            return Err(knowledge_unavailable())

        model_dim = self._embedder.dimension()
        if collection_dim is not None and collection_dim != model_dim:
            return Err(embedding_dimension_mismatch(expected=collection_dim, actual=model_dim))

        report = IngestReport()
        for source in command.files:
            report.documents.append(await self._ingest_one(source, force=command.force))

        if command.prune_missing:
            report.pruned_document_ids.extend(await self._prune(command.files))

        logger.info(
            "kb.ingest.completed",
            ingested=report.ingested,
            skipped=report.skipped,
            failed=report.failed,
            pruned=len(report.pruned_document_ids),
        )
        return Ok(report)

    async def _ingest_one(self, source: SourceFile, *, force: bool) -> DocumentIngestReport:
        try:
            doc = await self._loader.load(source)
        except DocumentLoadError as exc:
            logger.warning("kb.ingest.load_failed", document_id=source.document_id, error=str(exc))
            return DocumentIngestReport(
                document_id=source.document_id,
                title=source.title,
                status="failed",
                detail=str(exc),
            )

        try:
            existing = await self._store.document_state(doc.document_id)
            if not force and existing is not None and existing.content_hash == doc.content_hash:
                logger.info(
                    "kb.ingest.skipped_unchanged",
                    document_id=doc.document_id,
                    content_hash=doc.content_hash,
                )
                return DocumentIngestReport(
                    document_id=doc.document_id,
                    title=doc.title,
                    status="skipped",
                    version=existing.version,
                )

            version = (existing.version + 1) if existing else 1
            chunks = chunk_document(doc, version=version, ingested_at=self._clock.now())
            embeddings = await self._embedder.embed_documents([c.text for c in chunks])
            await self._store.replace_document(doc.document_id, chunks, embeddings)
        except (VectorStoreError, EmbeddingError) as exc:
            logger.warning("kb.ingest.store_failed", document_id=doc.document_id, error=str(exc))
            return DocumentIngestReport(
                document_id=doc.document_id,
                title=doc.title,
                status="failed",
                detail="knowledge base unavailable",
            )

        await self._events.publish(
            DocumentIngested(
                event_id=self._ids.new_id(),
                occurred_at=self._clock.now(),
                document_id=doc.document_id,
                title=doc.title,
                source_type=doc.source_type.value,
                document_version=version,
                chunk_count=len(chunks),
                content_hash=doc.content_hash,
            )
        )
        logger.info(
            "kb.ingest.document",
            document_id=doc.document_id,
            version=version,
            chunk_count=len(chunks),
        )
        return DocumentIngestReport(
            document_id=doc.document_id,
            title=doc.title,
            status="ingested",
            version=version,
            chunk_count=len(chunks),
        )

    async def _prune(self, files: tuple[SourceFile, ...]) -> list[str]:
        present = {f.document_id for f in files}
        try:
            stored = await self._store.list_document_ids()
        except VectorStoreError:
            return []
        stale = sorted(stored - present)
        for document_id in stale:
            await self._store.delete_document(document_id)
            logger.info("kb.ingest.pruned", document_id=document_id)
        return stale
