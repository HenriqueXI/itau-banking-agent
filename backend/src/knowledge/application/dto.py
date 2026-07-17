"""Commands and results crossing the knowledge application boundary."""

from dataclasses import dataclass, field
from pathlib import Path

from knowledge.domain.values import SourceType


@dataclass(frozen=True, kw_only=True)
class SourceFile:
    """A file to ingest, tagged with the identity used for citations/idempotency."""

    path: Path
    source_type: SourceType
    document_id: str
    title: str


@dataclass(frozen=True, kw_only=True)
class IngestCommand:
    files: tuple[SourceFile, ...]
    prune_missing: bool = False
    force: bool = False


@dataclass(frozen=True, kw_only=True)
class DocumentIngestReport:
    document_id: str
    title: str
    status: str  # "ingested" | "skipped" | "failed"
    version: int | None = None
    chunk_count: int = 0
    detail: str | None = None


@dataclass(frozen=True, kw_only=True)
class IngestReport:
    documents: list[DocumentIngestReport] = field(default_factory=list)
    pruned_document_ids: list[str] = field(default_factory=list)

    @property
    def ingested(self) -> int:
        return sum(1 for d in self.documents if d.status == "ingested")

    @property
    def skipped(self) -> int:
        return sum(1 for d in self.documents if d.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for d in self.documents if d.status == "failed")


@dataclass(frozen=True, kw_only=True)
class RetrieveQuery:
    text: str
    source_type: SourceType | None = None
    top_k: int | None = None
