"""Retrieve use case: relevance floor, dedupe, token cap, unavailability (PRD002-FR-6).

Scores are injected via a stub store so each rule is asserted against controlled
numbers rather than an embedding model's judgement.
"""

from datetime import UTC, datetime

import pytest

from knowledge.application.dto import RetrieveQuery
from knowledge.application.ports.embedding import EmbeddingError
from knowledge.application.ports.vector_store import VectorStoreError
from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge
from knowledge.domain.values import Chunk, ChunkMetadata, ScoredChunk, SourceType
from shared.domain.result import is_err, is_ok

FLOOR = 0.35


def _chunk(text: str, *, document_id: str = "doc", section: str = "S", page: int | None = None):
    return Chunk(
        id=f"{document_id}:{section}:{hash(text) % 1000}",
        text=text,
        metadata=ChunkMetadata(
            document_id=document_id,
            title="Título",
            source_type=SourceType.TARIFF,
            section=section,
            chunk_index=0,
            content_hash="h",
            version=1,
            ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
            page=page,
        ),
    )


class StubStore:
    """Returns a fixed, pre-scored hit list; records the query arguments."""

    def __init__(self, hits: list[ScoredChunk]) -> None:
        self._hits = hits
        self.calls: list[dict] = []

    async def query(self, embedding, *, top_k, source_type):
        self.calls.append({"top_k": top_k, "source_type": source_type})
        return [
            h
            for h in self._hits
            if source_type is None or h.chunk.metadata.source_type is source_type
        ][:top_k]

    async def collection_dimension(self):  # pragma: no cover - unused here
        raise NotImplementedError

    async def document_state(self, document_id):  # pragma: no cover - unused here
        raise NotImplementedError

    async def list_document_ids(self):  # pragma: no cover - unused here
        raise NotImplementedError

    async def replace_document(self, document_id, chunks, embeddings):  # pragma: no cover
        raise NotImplementedError

    async def delete_document(self, document_id):  # pragma: no cover - unused here
        raise NotImplementedError


class StubEmbedder:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def dimension(self) -> int:
        return 8

    async def embed_documents(self, texts):  # pragma: no cover - unused here
        return [[0.0] * 8 for _ in texts]

    async def embed_query(self, text):
        if self._error:
            raise self._error
        return [1.0] + [0.0] * 7


def _use_case(store, embedder=None, *, token_cap=2000, dedupe=0.97, top_k=6) -> RetrieveKnowledge:
    return RetrieveKnowledge(
        embedder=embedder or StubEmbedder(),
        store=store,
        top_k=top_k,
        relevance_floor=FLOOR,
        context_token_cap=token_cap,
        dedupe_similarity=dedupe,
    )


class TestRelevanceFloor:
    """BR-8.3: below the floor the agent refuses — evidence must be empty."""

    async def test_below_floor_returns_zero_chunks(self) -> None:
        store = StubStore([ScoredChunk(chunk=_chunk("algo irrelevante"), score=0.20)])

        result = await _use_case(store).execute(RetrieveQuery(text="financiamento de iate"))

        assert is_ok(result)
        outcome = result.value
        assert outcome.below_floor is True
        assert outcome.chunks == ()
        assert outcome.citations == ()
        assert outcome.best_score == pytest.approx(0.20)

    async def test_no_hits_at_all_is_below_floor(self) -> None:
        result = await _use_case(StubStore([])).execute(RetrieveQuery(text="nada"))

        assert is_ok(result)
        assert result.value.below_floor is True
        assert result.value.best_score is None

    async def test_score_exactly_at_floor_is_grounded(self) -> None:
        store = StubStore([ScoredChunk(chunk=_chunk("taxa 1,80%"), score=FLOOR)])

        result = await _use_case(store).execute(RetrieveQuery(text="taxa"))

        assert is_ok(result)
        assert result.value.below_floor is False
        assert len(result.value.chunks) == 1

    async def test_best_above_floor_keeps_lower_scored_hits_as_context(self) -> None:
        store = StubStore(
            [
                ScoredChunk(chunk=_chunk("taxa consignado 1,80%"), score=0.80),
                ScoredChunk(chunk=_chunk("margem consignável 35%"), score=0.10),
            ]
        )

        result = await _use_case(store).execute(RetrieveQuery(text="consignado"))

        assert is_ok(result)
        assert len(result.value.chunks) == 2


class TestCitations:
    async def test_citations_carry_document_section_and_page(self) -> None:
        store = StubStore(
            [
                ScoredChunk(
                    chunk=_chunk("taxa", document_id="tarifas", section="Taxas", page=4), score=0.9
                )
            ]
        )

        result = await _use_case(store).execute(RetrieveQuery(text="taxa"))

        assert is_ok(result)
        citation = result.value.citations[0]
        assert citation.document_id == "tarifas"
        assert citation.section == "Taxas"
        assert citation.page == 4
        assert citation.marker() == "【Título — p.4】"


class TestDedupe:
    async def test_near_identical_chunks_collapse_to_the_highest_scored(self) -> None:
        text = "A taxa do consignado para aposentados é de 1,80% ao mês"
        store = StubStore(
            [
                ScoredChunk(chunk=_chunk(text, document_id="a"), score=0.90),
                ScoredChunk(chunk=_chunk(text, document_id="b"), score=0.85),
            ]
        )

        result = await _use_case(store).execute(RetrieveQuery(text="taxa"))

        assert is_ok(result)
        assert len(result.value.chunks) == 1
        assert result.value.chunks[0].score == pytest.approx(0.90)

    async def test_distinct_chunks_are_all_kept(self) -> None:
        store = StubStore(
            [
                ScoredChunk(chunk=_chunk("taxa do consignado para aposentados"), score=0.90),
                ScoredChunk(chunk=_chunk("anuidade do cartão platinum é 600"), score=0.60),
            ]
        )

        result = await _use_case(store).execute(RetrieveQuery(text="taxa"))

        assert is_ok(result)
        assert len(result.value.chunks) == 2


class TestTokenCap:
    async def test_cap_drops_lowest_scored_chunks(self) -> None:
        big = " ".join(["palavra"] * 100)  # ~130 tokens each
        store = StubStore(
            [
                ScoredChunk(chunk=_chunk(f"primeiro {big}"), score=0.90),
                ScoredChunk(chunk=_chunk(f"segundo {big}"), score=0.80),
                ScoredChunk(chunk=_chunk(f"terceiro {big}"), score=0.70),
            ]
        )

        result = await _use_case(store, token_cap=200).execute(RetrieveQuery(text="q"))

        assert is_ok(result)
        assert len(result.value.chunks) == 1
        assert result.value.chunks[0].score == pytest.approx(0.90)

    async def test_single_oversized_chunk_is_still_returned(self) -> None:
        store = StubStore([ScoredChunk(chunk=_chunk(" ".join(["palavra"] * 5000)), score=0.90)])

        result = await _use_case(store, token_cap=100).execute(RetrieveQuery(text="q"))

        assert is_ok(result)
        assert len(result.value.chunks) == 1, "never return zero evidence above the floor"


class TestFiltersAndDepth:
    async def test_source_type_filter_is_passed_through(self) -> None:
        store = StubStore([ScoredChunk(chunk=_chunk("taxa"), score=0.9)])

        await _use_case(store).execute(RetrieveQuery(text="taxa", source_type=SourceType.TARIFF))

        assert store.calls[0]["source_type"] is SourceType.TARIFF

    async def test_query_top_k_overrides_the_configured_default(self) -> None:
        store = StubStore([ScoredChunk(chunk=_chunk("taxa"), score=0.9)])

        await _use_case(store, top_k=6).execute(RetrieveQuery(text="taxa", top_k=2))

        assert store.calls[0]["top_k"] == 2


class TestUnavailability:
    """rag.md §7: infrastructure failure is typed and loud — never a silent empty answer."""

    async def test_vector_store_failure_maps_to_knowledge_unavailable(self) -> None:
        class FailingStore(StubStore):
            async def query(self, embedding, *, top_k, source_type):
                raise VectorStoreError("chroma down")

        result = await _use_case(FailingStore([])).execute(RetrieveQuery(text="taxa"))

        assert is_err(result)
        assert result.error.code == "knowledge.unavailable"

    async def test_embedding_failure_maps_to_knowledge_unavailable(self) -> None:
        use_case = _use_case(StubStore([]), StubEmbedder(error=EmbeddingError("gemini down")))

        result = await use_case.execute(RetrieveQuery(text="taxa"))

        assert is_err(result)
        assert result.error.code == "knowledge.unavailable"
