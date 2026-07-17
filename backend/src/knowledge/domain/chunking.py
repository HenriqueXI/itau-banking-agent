"""Chunking policies per source type (rag.md §1) — pure, deterministic.

Policies:
  * tariff     — keep tables whole; one chunk per fee section, split only on
                 blank lines when a section exceeds the cap (never mid-table).
  * faq        — one Q&A pair per chunk (loader already splits pairs into sections).
  * regulation — heading-based sliding window, 400-700 tokens, ~15% overlap.

Token counts use a deterministic word-based estimate so chunking is reproducible
without a tokenizer dependency in the domain layer.
"""

from datetime import datetime

from knowledge.domain.document import DocumentSection, LoadedDocument
from knowledge.domain.values import Chunk, ChunkMetadata, SourceType

_TOKENS_PER_WORD = 1.3

# Per-policy token windows (rag.md §1).
_TARIFF_MAX = 500
_REGULATION_TARGET = 700
_REGULATION_OVERLAP = 0.15


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (no tokenizer dependency in domain)."""
    return max(1, round(len(text.split()) * _TOKENS_PER_WORD))


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|") or ("|" in line and line.strip().endswith("|"))


def _split_tariff_section(text: str) -> list[str]:
    """Split on blank lines but never break a contiguous markdown table block."""
    if estimate_tokens(text) <= _TARIFF_MAX:
        return [text.strip()]

    blocks: list[str] = []
    current: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        candidate = "\n\n".join([*current, para])
        # Keep growing while under cap OR while inside a table (tables stay whole).
        if current and estimate_tokens(candidate) > _TARIFF_MAX and not _is_table_line(para):
            blocks.append("\n\n".join(current))
            current = [para]
        else:
            current.append(para)
    if current:
        blocks.append("\n\n".join(current))
    return blocks


def _split_regulation_section(text: str) -> list[str]:
    """Sliding word window: ~700-token chunks with ~15% overlap."""
    words = text.split()
    if estimate_tokens(text) <= _REGULATION_TARGET:
        return [text.strip()]

    window = int(_REGULATION_TARGET / _TOKENS_PER_WORD)
    step = max(1, int(window * (1 - _REGULATION_OVERLAP)))
    windows: list[str] = []
    start = 0
    while start < len(words):
        windows.append(" ".join(words[start : start + window]))
        if start + window >= len(words):
            break
        start += step
    return windows


def _section_texts(section: DocumentSection, source_type: SourceType) -> list[str]:
    if source_type is SourceType.FAQ:
        return [section.text.strip()]
    if source_type is SourceType.TARIFF:
        return _split_tariff_section(section.text)
    return _split_regulation_section(section.text)


def chunk_document(doc: LoadedDocument, *, version: int, ingested_at: datetime) -> list[Chunk]:
    """Apply the source-type policy, producing citation-ready chunks."""
    chunks: list[Chunk] = []
    index = 0
    for section in doc.sections:
        for text in _section_texts(section, doc.source_type):
            if not text.strip():
                continue
            metadata = ChunkMetadata(
                document_id=doc.document_id,
                title=doc.title,
                source_type=doc.source_type,
                section=section.heading,
                chunk_index=index,
                content_hash=doc.content_hash,
                version=version,
                ingested_at=ingested_at,
                page=section.page,
            )
            chunks.append(
                Chunk(id=f"{doc.document_id}:v{version}:{index}", text=text, metadata=metadata)
            )
            index += 1
    return chunks
