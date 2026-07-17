"""Ingestion use case: idempotency, versioning, failure isolation (PRD002-FR-5)."""

from pathlib import Path

import pytest
from tests.fakes.knowledge import InMemoryVectorStore, LexicalEmbedder
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

from knowledge.adapters.outbound.loaders.corpus import scan_corpus
from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
from knowledge.application.dto import IngestCommand, SourceFile
from knowledge.application.ports.document_loader import DocumentLoadError
from knowledge.application.ports.vector_store import VectorStoreError
from knowledge.application.use_cases.ingest_knowledge_base import IngestKnowledgeBase
from knowledge.domain.values import SourceType
from shared.domain.result import is_err, is_ok

KB = Path(__file__).resolve().parents[2] / "fixtures" / "kb"


class CountingEmbedder(LexicalEmbedder):
    """Counts how many chunks were embedded — the idempotency probe."""

    def __init__(self) -> None:
        super().__init__()
        self.embedded = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return await super().embed_documents(texts)


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def events() -> RecordingEventPublisher:
    return RecordingEventPublisher()


@pytest.fixture
def use_case(embedder, store, events) -> IngestKnowledgeBase:
    return IngestKnowledgeBase(
        loader=LangChainDocumentLoader(),
        embedder=embedder,
        store=store,
        events=events,
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
    )


def _corpus() -> IngestCommand:
    return IngestCommand(files=scan_corpus(KB))


class TestIdempotency:
    async def test_reingesting_unchanged_corpus_embeds_nothing(
        self, use_case, embedder, store
    ) -> None:
        first = await use_case.execute(_corpus())
        assert is_ok(first)
        assert first.value.ingested == len(scan_corpus(KB))
        documents_after_first = await store.list_document_ids()
        embedded_first = embedder.embedded
        assert embedded_first > 0

        second = await use_case.execute(_corpus())

        assert is_ok(second)
        assert embedder.embedded == embedded_first, "unchanged documents must not re-embed"
        assert second.value.ingested == 0
        assert second.value.skipped == len(scan_corpus(KB))
        assert await store.list_document_ids() == documents_after_first, "no duplicates"

    async def test_force_reembeds_unchanged_corpus(self, use_case, embedder, store) -> None:
        first = await use_case.execute(_corpus())
        assert is_ok(first)
        embedded_first = embedder.embedded

        forced = await use_case.execute(IngestCommand(files=scan_corpus(KB), force=True))

        assert is_ok(forced)
        assert forced.value.ingested == len(scan_corpus(KB))
        assert forced.value.skipped == 0
        assert embedder.embedded == embedded_first * 2
        state = await store.document_state("tarifas_consignado")
        assert state is not None
        assert state.version == 2

    async def test_changed_document_replaces_prior_version(self, use_case, store, tmp_path) -> None:
        document = tmp_path / "tariff" / "taxas.md"
        document.parent.mkdir(parents=True)
        document.write_text("# Taxas\n\n## Consignado\n\nTaxa de 1,80% ao mês.", encoding="utf-8")
        command = IngestCommand(files=scan_corpus(tmp_path))

        await use_case.execute(command)
        document.write_text("# Taxas\n\n## Consignado\n\nTaxa de 2,50% ao mês.", encoding="utf-8")
        second = await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        assert is_ok(second)
        assert second.value.ingested == 1
        state = await store.document_state("taxas")
        assert state is not None
        assert state.version == 2, "changed content bumps the version"
        # No stale chunks: the old rate must be gone, not merely shadowed.
        hits = await store.query(
            await LexicalEmbedder().embed_query("taxa consignado"), top_k=20, source_type=None
        )
        texts = " ".join(h.chunk.text for h in hits)
        assert "2,50%" in texts
        assert "1,80%" not in texts

    async def test_new_document_is_version_one(self, use_case, store) -> None:
        await use_case.execute(_corpus())

        state = await store.document_state("regras_pix")
        assert state is not None
        assert state.version == 1


class TestFailureIsolation:
    """One unparseable file must not abort the batch (PRD-002 edge case)."""

    async def test_bad_document_is_reported_and_others_proceed(
        self, embedder, store, events
    ) -> None:
        class PartlyFailingLoader(LangChainDocumentLoader):
            async def load(self, source: SourceFile):
                if source.document_id == "faq_pix":
                    raise DocumentLoadError("unparseable layout")
                return await super().load(source)

        use_case = IngestKnowledgeBase(
            loader=PartlyFailingLoader(),
            embedder=embedder,
            store=store,
            events=events,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
        )

        result = await use_case.execute(_corpus())

        assert is_ok(result)
        report = result.value
        assert report.failed == 1
        assert report.ingested == len(scan_corpus(KB)) - 1
        failure = next(d for d in report.documents if d.status == "failed")
        assert failure.document_id == "faq_pix"
        assert "unparseable" in (failure.detail or "")
        assert "faq_pix" not in await store.list_document_ids()

    async def test_store_failure_marks_document_failed_not_the_batch(
        self, embedder, events
    ) -> None:
        class FailingStore(InMemoryVectorStore):
            async def replace_document(self, document_id, chunks, embeddings):
                if document_id == "regras_pix":
                    raise VectorStoreError("chroma down")
                await super().replace_document(document_id, chunks, embeddings)

        use_case = IngestKnowledgeBase(
            loader=LangChainDocumentLoader(),
            embedder=embedder,
            store=FailingStore(),
            events=events,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
        )

        result = await use_case.execute(_corpus())

        assert is_ok(result)
        assert result.value.failed == 1
        assert result.value.ingested == len(scan_corpus(KB)) - 1


class TestDimensionGuard:
    """Provider switch must not silently mix vector spaces (PRD-002 edge case)."""

    async def test_mismatched_dimension_refuses_before_writing(self, store, events) -> None:
        await IngestKnowledgeBase(
            loader=LangChainDocumentLoader(),
            embedder=LexicalEmbedder(dimension=256),
            store=store,
            events=events,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
        ).execute(_corpus())
        documents_before = await store.list_document_ids()

        result = await IngestKnowledgeBase(
            loader=LangChainDocumentLoader(),
            embedder=LexicalEmbedder(dimension=128),
            store=store,
            events=events,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
        ).execute(_corpus())

        assert is_err(result)
        assert result.error.code == "knowledge.embedding_dimension_mismatch"
        assert await store.list_document_ids() == documents_before, "no partial writes"

    async def test_empty_collection_accepts_any_dimension(self, store, events) -> None:
        result = await IngestKnowledgeBase(
            loader=LangChainDocumentLoader(),
            embedder=LexicalEmbedder(dimension=64),
            store=store,
            events=events,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
        ).execute(_corpus())

        assert is_ok(result)


class TestPruning:
    async def test_prune_removes_documents_missing_from_source(
        self, use_case, store, tmp_path
    ) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        (directory / "a.md").write_text("# A\n\n## Q?\n\nResposta.", encoding="utf-8")
        (directory / "b.md").write_text("# B\n\n## Q?\n\nResposta.", encoding="utf-8")
        await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        (directory / "b.md").unlink()
        result = await use_case.execute(
            IngestCommand(files=scan_corpus(tmp_path), prune_missing=True)
        )

        assert is_ok(result)
        assert result.value.pruned_document_ids == ["b"]
        assert await store.list_document_ids() == {"a"}

    async def test_without_prune_stale_documents_survive(self, use_case, store, tmp_path) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        (directory / "a.md").write_text("# A\n\n## Q?\n\nResposta.", encoding="utf-8")
        (directory / "b.md").write_text("# B\n\n## Q?\n\nResposta.", encoding="utf-8")
        await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        (directory / "b.md").unlink()
        await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        assert await store.list_document_ids() == {"a", "b"}


class TestEvents:
    async def test_document_ingested_published_per_ingested_document(
        self, use_case, events
    ) -> None:
        await use_case.execute(_corpus())

        assert len(events.events) == len(scan_corpus(KB))
        event = next(e for e in events.events if e.document_id == "tarifas_consignado")
        assert event.event_type == "knowledge.DocumentIngested"
        assert event.source_type == SourceType.TARIFF.value
        assert event.version == 1
        assert event.chunk_count > 0
        assert event.content_hash

    async def test_skipped_documents_publish_no_event(self, use_case, events) -> None:
        await use_case.execute(_corpus())
        events.events.clear()

        await use_case.execute(_corpus())

        assert events.events == []
