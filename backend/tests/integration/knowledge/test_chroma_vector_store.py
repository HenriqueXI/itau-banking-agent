"""Chroma adapter against a real server: idempotency, versioning, filters, citations.

Embeddings come from the deterministic lexical fake — Chroma stores whatever
vectors we hand it, so these tests need no embedding provider and stay reproducible.
"""

import uuid
from pathlib import Path

import pytest

from knowledge.adapters.outbound.chroma.vector_store import ChromaVectorStore
from knowledge.adapters.outbound.loaders.corpus import scan_corpus
from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
from knowledge.application.dto import IngestCommand, RetrieveQuery
from knowledge.application.use_cases.ingest_knowledge_base import IngestKnowledgeBase
from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge
from knowledge.domain.values import SourceType
from shared.config import Settings
from shared.domain.result import is_ok
from tests.fakes.knowledge import LexicalEmbedder
from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

pytestmark = pytest.mark.integration

KB = Path(__file__).resolve().parents[2] / "fixtures" / "kb"


@pytest.fixture
def store(chroma_url: str) -> ChromaVectorStore:
    # A fresh collection per test keeps them independent.
    return ChromaVectorStore(url=chroma_url, collection=f"kb_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def embedder() -> LexicalEmbedder:
    return LexicalEmbedder()


def _ingest_use_case(store, embedder, events=None) -> IngestKnowledgeBase:
    return IngestKnowledgeBase(
        loader=LangChainDocumentLoader(),
        embedder=embedder,
        store=store,
        events=events or RecordingEventPublisher(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
    )


def _retrieve_use_case(store, embedder, floor: float = 0.0) -> RetrieveKnowledge:
    return RetrieveKnowledge(
        embedder=embedder,
        store=store,
        top_k=6,
        relevance_floor=floor,
        context_token_cap=2000,
        dedupe_similarity=0.97,
    )


class TestIngestionIdempotency:
    """PRD-002 acceptance: re-ingesting unchanged content duplicates nothing."""

    async def test_second_ingest_skips_every_document(self, store, embedder) -> None:
        use_case = _ingest_use_case(store, embedder)
        command = IngestCommand(files=scan_corpus(KB))
        first = await use_case.execute(command)
        assert is_ok(first)
        assert first.value.ingested == len(scan_corpus(KB))

        second = await use_case.execute(command)

        assert is_ok(second)
        assert second.value.skipped == len(scan_corpus(KB))
        assert second.value.ingested == 0

    async def test_no_duplicate_chunks_after_reingest(self, store, embedder) -> None:
        use_case = _ingest_use_case(store, embedder)
        command = IngestCommand(files=scan_corpus(KB))
        await use_case.execute(command)
        before = await store.query(
            await embedder.embed_query("taxa consignado aposentados"), top_k=50, source_type=None
        )

        await use_case.execute(command)
        after = await store.query(
            await embedder.embed_query("taxa consignado aposentados"), top_k=50, source_type=None
        )

        assert len(after) == len(before)
        assert {c.chunk.id for c in after} == {c.chunk.id for c in before}

    async def test_changed_document_leaves_no_stale_chunks(
        self, store, embedder, tmp_path: Path
    ) -> None:
        directory = tmp_path / "tariff"
        directory.mkdir(parents=True)
        path = directory / "taxas.md"
        path.write_text("# Taxas\n\n## Consignado\n\nTaxa de 1,80% ao mês.", encoding="utf-8")
        use_case = _ingest_use_case(store, embedder)
        await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        path.write_text("# Taxas\n\n## Consignado\n\nTaxa de 2,50% ao mês.", encoding="utf-8")
        await use_case.execute(IngestCommand(files=scan_corpus(tmp_path)))

        hits = await store.query(
            await embedder.embed_query("taxa consignado"), top_k=50, source_type=None
        )
        texts = " ".join(h.chunk.text for h in hits)
        assert "2,50%" in texts
        assert "1,80%" not in texts, "previous version's chunks must be deleted"
        state = await store.document_state("taxas")
        assert state is not None and state.version == 2


class TestRetrieval:
    async def test_citation_metadata_survives_the_round_trip(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        result = await _retrieve_use_case(store, embedder).execute(
            RetrieveQuery(text="taxa do empréstimo consignado para aposentados")
        )

        assert is_ok(result)
        outcome = result.value
        assert outcome.chunks, "expected evidence above a zero floor"
        from_tariff = [
            sc for sc in outcome.chunks if sc.chunk.metadata.document_id == "tarifas_consignado"
        ]
        assert from_tariff, "the consignado tariff document must be retrieved"
        meta = from_tariff[0].chunk.metadata
        assert meta.title == "Tarifas de Empréstimo Consignado"
        assert meta.source_type is SourceType.TARIFF
        assert meta.section
        assert meta.version == 1
        assert meta.content_hash
        assert meta.ingested_at.tzinfo is not None

    async def test_source_type_filter_restricts_results(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        result = await _retrieve_use_case(store, embedder).execute(
            RetrieveQuery(text="limite", source_type=SourceType.FAQ)
        )

        assert is_ok(result)
        assert result.value.chunks
        assert all(sc.chunk.metadata.source_type is SourceType.FAQ for sc in result.value.chunks)

    async def test_scores_are_similarities_in_unit_range(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        result = await _retrieve_use_case(store, embedder).execute(
            RetrieveQuery(text="taxa do consignado")
        )

        assert is_ok(result)
        scores = [sc.score for sc in result.value.chunks]
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert scores == sorted(scores, reverse=True), "hits arrive ranked"

    async def test_unrelated_query_falls_below_a_configured_floor(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        result = await _retrieve_use_case(store, embedder, floor=0.99).execute(
            RetrieveQuery(text="financiamento de iate")
        )

        assert is_ok(result)
        assert result.value.below_floor is True
        assert result.value.chunks == (), "refusal path gets zero evidence"


class TestPruning:
    async def test_delete_document_removes_its_chunks(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))
        assert "faq_pix" in await store.list_document_ids()

        await store.delete_document("faq_pix")

        assert "faq_pix" not in await store.list_document_ids()


class TestConfiguredCollectionName:
    """The shipped default must be a name Chroma actually accepts.

    Regression: the default was `kb`, which Chroma rejects (names need 3+ chars).
    Every other test here uses a generated `kb_<uuid>` name, so none of them
    exercised the configured value and ingestion failed only on a real run.
    """

    async def test_default_collection_name_is_accepted_by_chroma(self, chroma_url) -> None:
        default_name = Settings.model_fields["chroma_collection"].default

        store = ChromaVectorStore(url=chroma_url, collection=default_name)

        assert await store.collection_dimension() is None  # connects, empty collection


class TestDimensionGuard:
    async def test_collection_dimension_reflects_stored_vectors(self, store, embedder) -> None:
        assert await store.collection_dimension() is None

        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        assert await store.collection_dimension() == embedder.dimension()

    async def test_switching_dimension_is_refused(self, store, embedder) -> None:
        await _ingest_use_case(store, embedder).execute(IngestCommand(files=scan_corpus(KB)))

        result = await _ingest_use_case(store, LexicalEmbedder(dimension=128)).execute(
            IngestCommand(files=scan_corpus(KB))
        )

        assert not is_ok(result)
        assert result.error.code == "knowledge.embedding_dimension_mismatch"
