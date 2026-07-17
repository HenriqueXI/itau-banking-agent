"""Chunking policies per source type (rag.md §1) — asserted on the real fixtures."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge.adapters.outbound.loaders.corpus import scan_corpus
from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
from knowledge.domain.chunking import chunk_document, estimate_tokens
from knowledge.domain.document import DocumentSection, LoadedDocument
from knowledge.domain.values import SourceType

KB = Path(__file__).resolve().parents[2] / "fixtures" / "kb"
INGESTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


async def _load(document_id: str) -> LoadedDocument:
    source = next(f for f in scan_corpus(KB) if f.document_id == document_id)
    return await LangChainDocumentLoader().load(source)


def _document(source_type: SourceType, sections: list[DocumentSection]) -> LoadedDocument:
    return LoadedDocument(
        document_id="doc",
        title="Doc",
        source_type=source_type,
        sections=tuple(sections),
        content_hash="hash",
    )


class TestFaqPolicy:
    """One Q&A pair per chunk — the natural semantic unit."""

    async def test_each_qa_pair_is_one_chunk(self) -> None:
        doc = await _load("faq_pix")
        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        # The loader sections on headings; every Q&A heading becomes exactly one chunk.
        assert len(chunks) == len(doc.sections)
        assert any(c.metadata.section == "O PIX tem custo para pessoa física?" for c in chunks)

    async def test_qa_pair_is_never_split_even_when_long(self) -> None:
        long_answer = " ".join(["palavra"] * 2000)
        doc = _document(SourceType.FAQ, [DocumentSection(heading="Q?", text=long_answer)])

        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        assert len(chunks) == 1


class TestTariffPolicy:
    """Fee sections chunked; tables must survive whole (they carry the rates)."""

    async def test_table_rows_stay_in_one_chunk(self) -> None:
        doc = await _load("tarifas_consignado")
        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        table_chunks = [c for c in chunks if "1,80% a.m." in c.text]
        assert len(table_chunks) == 1
        # Every segment row of the rate table lands in that same chunk.
        for rate in ("1,80% a.m.", "1,65% a.m.", "2,10% a.m."):
            assert rate in table_chunks[0].text

    async def test_sections_become_separate_chunks(self) -> None:
        doc = await _load("tarifas_consignado")
        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        sections = {c.metadata.section for c in chunks}
        assert {"Taxas por segmento", "Margem consignável"} <= sections

    def test_oversized_prose_section_is_split(self) -> None:
        paragraphs = "\n\n".join(" ".join(["palavra"] * 200) for _ in range(6))
        doc = _document(SourceType.TARIFF, [DocumentSection(heading="Tarifas", text=paragraphs)])

        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        assert len(chunks) > 1
        assert all(estimate_tokens(c.text) <= 500 for c in chunks)


class TestRegulationPolicy:
    """Heading-based split, ~700 tokens with overlap so rules aren't cut mid-sentence."""

    async def test_headings_drive_sections(self) -> None:
        doc = await _load("regras_pix")
        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        sections = {c.metadata.section for c in chunks}
        assert "Limite diário" in sections
        assert "Ajuste de limites" in sections

    def test_long_section_windows_overlap(self) -> None:
        words = [f"w{i}" for i in range(2000)]
        doc = _document(
            SourceType.REGULATION, [DocumentSection(heading="Regra", text=" ".join(words))]
        )

        chunks = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

        assert len(chunks) > 1
        first, second = chunks[0].text.split(), chunks[1].text.split()
        assert set(first) & set(second), "consecutive windows must overlap"

    def test_short_section_is_a_single_chunk(self) -> None:
        doc = _document(
            SourceType.REGULATION, [DocumentSection(heading="Regra", text="Limite de R$ 5.000,00.")]
        )

        assert len(chunk_document(doc, version=1, ingested_at=INGESTED_AT)) == 1


class TestChunkMetadata:
    """Metadata completeness is what makes a chunk citable (PRD002-FR-4)."""

    async def test_every_chunk_carries_citation_metadata(self) -> None:
        doc = await _load("tarifas_consignado")

        chunks = chunk_document(doc, version=3, ingested_at=INGESTED_AT)

        for index, chunk in enumerate(chunks):
            meta = chunk.metadata
            assert meta.document_id == "tarifas_consignado"
            assert meta.title
            assert meta.source_type is SourceType.TARIFF
            assert meta.section
            assert meta.chunk_index == index
            assert meta.content_hash == doc.content_hash
            assert meta.version == 3
            assert meta.ingested_at == INGESTED_AT

    async def test_chunk_ids_are_unique_and_version_scoped(self) -> None:
        doc = await _load("regras_pix")

        chunks = chunk_document(doc, version=2, ingested_at=INGESTED_AT)

        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))
        assert all(cid.startswith("regras_pix:v2:") for cid in ids)

    def test_empty_sections_produce_no_chunks(self) -> None:
        doc = _document(SourceType.FAQ, [DocumentSection(heading="Vazio", text="   ")])

        assert chunk_document(doc, version=1, ingested_at=INGESTED_AT) == []


@pytest.mark.parametrize("document_id", ["tarifas_consignado", "faq_pix", "regras_pix"])
async def test_chunking_is_deterministic(document_id: str) -> None:
    doc = await _load(document_id)

    first = chunk_document(doc, version=1, ingested_at=INGESTED_AT)
    second = chunk_document(doc, version=1, ingested_at=INGESTED_AT)

    assert [c.text for c in first] == [c.text for c in second]
    assert [c.id for c in first] == [c.id for c in second]
