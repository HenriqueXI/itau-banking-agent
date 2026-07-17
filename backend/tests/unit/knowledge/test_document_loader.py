"""LangChain loader adapter + corpus scanning (PRD002-FR-1)."""

from pathlib import Path

import pytest

from knowledge.adapters.outbound.loaders.corpus import scan_corpus
from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
from knowledge.application.dto import SourceFile
from knowledge.application.ports.document_loader import DocumentLoadError
from knowledge.domain.document import LoadedDocument
from knowledge.domain.values import SourceType

KB = Path(__file__).resolve().parents[2] / "fixtures" / "kb"


@pytest.fixture
def loader() -> LangChainDocumentLoader:
    return LangChainDocumentLoader()


class TestCorpusScan:
    def test_source_type_comes_from_the_parent_directory(self) -> None:
        files = scan_corpus(KB)

        by_id = {f.document_id: f for f in files}
        assert by_id["tarifas_consignado"].source_type is SourceType.TARIFF
        assert by_id["faq_pix"].source_type is SourceType.FAQ
        assert by_id["regras_pix"].source_type is SourceType.REGULATION

    def test_title_comes_from_the_first_heading(self) -> None:
        files = {f.document_id: f for f in scan_corpus(KB)}

        assert files["tarifas_consignado"].title == "Tarifas de Empréstimo Consignado"

    def test_scan_is_deterministic(self) -> None:
        assert [f.document_id for f in scan_corpus(KB)] == [f.document_id for f in scan_corpus(KB)]

    def test_unsupported_files_are_ignored(self, tmp_path: Path) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        (directory / "notes.docx").write_bytes(b"binary")
        (directory / "real.md").write_text("# Real", encoding="utf-8")

        assert [f.document_id for f in scan_corpus(tmp_path)] == ["real"]

    def test_missing_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert scan_corpus(tmp_path) == ()


class TestMarkdownLoading:
    async def test_headings_become_sections(self, loader) -> None:
        source = next(f for f in scan_corpus(KB) if f.document_id == "regras_pix")

        doc = await loader.load(source)

        headings = [s.heading for s in doc.sections]
        assert "Limite diário" in headings
        assert "Verificação de segurança para valores elevados" in headings
        assert all(s.page is None for s in doc.sections), "markdown has no pages"

    async def test_content_hash_tracks_content_not_path(self, loader, tmp_path: Path) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        first = directory / "a.md"
        first.write_text("# A\n\nTexto igual.", encoding="utf-8")
        second = directory / "b.md"
        second.write_text("# A\n\nTexto igual.", encoding="utf-8")

        files = {f.document_id: f for f in scan_corpus(tmp_path)}
        doc_a = await loader.load(files["a"])
        doc_b = await loader.load(files["b"])

        assert doc_a.content_hash == doc_b.content_hash

    async def test_content_change_changes_the_hash(self, loader, tmp_path: Path) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        path = directory / "a.md"
        path.write_text("# A\n\nTaxa de 1,80%.", encoding="utf-8")
        source = scan_corpus(tmp_path)[0]
        before = (await loader.load(source)).content_hash

        path.write_text("# A\n\nTaxa de 2,50%.", encoding="utf-8")
        after = (await loader.load(source)).content_hash

        assert before != after

    async def test_whitespace_only_change_keeps_the_hash_stable(
        self, loader, tmp_path: Path
    ) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        path = directory / "a.md"
        path.write_text("# A\n\nTexto.", encoding="utf-8")
        source = scan_corpus(tmp_path)[0]
        before = (await loader.load(source)).content_hash

        path.write_text("# A\n\nTexto.\n\n", encoding="utf-8")
        after = (await loader.load(source)).content_hash

        assert before == after, "trailing whitespace must not force a re-embed"

    async def test_text_before_any_heading_keeps_the_title(self, loader, tmp_path: Path) -> None:
        directory = tmp_path / "faq"
        directory.mkdir(parents=True)
        (directory / "a.md").write_text("Preâmbulo sem título.\n\n# Depois\n\nCorpo.", "utf-8")
        source = scan_corpus(tmp_path)[0]

        doc = await loader.load(source)

        assert doc.sections[0].text.startswith("Preâmbulo")


class TestLoadFailures:
    async def test_unsupported_extension_raises_load_error(self, loader, tmp_path: Path) -> None:
        path = tmp_path / "doc.docx"
        path.write_bytes(b"binary")
        source = SourceFile(path=path, source_type=SourceType.FAQ, document_id="doc", title="Doc")

        with pytest.raises(DocumentLoadError) as exc_info:
            await loader.load(source)

        assert "unsupported extension" in str(exc_info.value)

    async def test_unparseable_pdf_raises_load_error(self, loader, tmp_path: Path) -> None:
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"not really a pdf")
        source = SourceFile(
            path=path, source_type=SourceType.TARIFF, document_id="broken", title="Broken"
        )

        # Typed, not a raw parser exception — the batch relies on catching this.
        with pytest.raises(DocumentLoadError):
            await loader.load(source)

    async def test_missing_file_raises_load_error(self, loader, tmp_path: Path) -> None:
        source = SourceFile(
            path=tmp_path / "gone.md",
            source_type=SourceType.FAQ,
            document_id="gone",
            title="Gone",
        )

        with pytest.raises(DocumentLoadError):
            await loader.load(source)


def test_hash_ignores_surrounding_whitespace() -> None:
    assert LoadedDocument.compute_hash("  texto  ") == LoadedDocument.compute_hash("texto")
