"""LangChain-backed document loader (PRD002-FR-1).

PDFs go through LangChain's PyPDFLoader (one Document per page → one section per
page, page number preserved for tariff citations). Markdown/TXT are read and
split into heading-based sections here (the domain owns chunking; the loader owns
parsing/sectioning). Parse failures surface as DocumentLoadError so the batch
survives one bad file.
"""

import re

from knowledge.application.dto import SourceFile
from knowledge.application.ports.document_loader import DocumentLoaderPort, DocumentLoadError
from knowledge.domain.document import DocumentSection, LoadedDocument

_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _split_markdown_sections(text: str, fallback_heading: str) -> list[DocumentSection]:
    """Split on markdown headings; text before the first heading keeps the title."""
    sections: list[DocumentSection] = []
    heading = fallback_heading
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            sections.append(DocumentSection(heading=heading, text=joined))

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            heading = match.group(1).strip()
            body = []
        else:
            body.append(line)
    flush()
    return sections or [DocumentSection(heading=fallback_heading, text=text.strip())]


class LangChainDocumentLoader(DocumentLoaderPort):
    async def load(self, source: SourceFile) -> LoadedDocument:
        suffix = source.path.suffix.lower()
        try:
            if suffix == ".pdf":
                sections, raw = self._load_pdf(source)
            elif suffix in (".md", ".txt", ".markdown"):
                raw = source.path.read_text(encoding="utf-8")
                sections = _split_markdown_sections(raw, source.title)
            else:
                raise DocumentLoadError(f"unsupported extension '{suffix}'")
        except DocumentLoadError:
            raise
        except Exception as exc:
            raise DocumentLoadError(str(exc)) from exc

        return LoadedDocument(
            document_id=source.document_id,
            title=source.title,
            source_type=source.source_type,
            sections=tuple(sections),
            content_hash=LoadedDocument.compute_hash(raw),
        )

    @staticmethod
    def _load_pdf(source: SourceFile) -> tuple[list[DocumentSection], str]:
        from langchain_community.document_loaders import PyPDFLoader

        pages = PyPDFLoader(str(source.path)).load()
        if not pages:
            raise DocumentLoadError("no extractable text (unparseable layout)")
        sections: list[DocumentSection] = []
        raw_parts: list[str] = []
        for page in pages:
            content = page.page_content.strip()
            if not content:
                continue
            page_no = int(page.metadata.get("page", 0)) + 1
            first_line = content.splitlines()[0].strip()
            sections.append(
                DocumentSection(
                    heading=first_line or f"Página {page_no}", text=content, page=page_no
                )
            )
            raw_parts.append(content)
        if not sections:
            raise DocumentLoadError("no extractable text (unparseable layout)")
        return sections, "\n".join(raw_parts)
