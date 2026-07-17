"""DocumentLoaderPort: parse a source file into a normalized LoadedDocument."""

from typing import Protocol

from knowledge.application.dto import SourceFile
from knowledge.domain.document import LoadedDocument


class DocumentLoadError(Exception):
    """A file could not be parsed (unreadable PDF layout, bad encoding).

    The ingestion use case catches this per-file so one bad document does not
    abort the batch (PRD-002 edge case).
    """


class DocumentLoaderPort(Protocol):
    async def load(self, source: SourceFile) -> LoadedDocument:
        """Parse `source` into sections + content hash. Raises DocumentLoadError."""
        ...
