"""Typed knowledge failures (returned via Result, never raised across layers)."""

from shared.domain.errors import DomainError


def knowledge_unavailable() -> DomainError:
    """Vector store / embedding provider unreachable — never answer from weights."""
    return DomainError(
        code="knowledge.unavailable",
        message="Knowledge base is unavailable (Chroma or embedding provider down)",
    )


def embedding_dimension_mismatch(expected: int, actual: int) -> DomainError:
    """Ingesting vectors of a different dimension than the collection (edge case)."""
    return DomainError(
        code="knowledge.embedding_dimension_mismatch",
        message=f"Embedding dimension {actual} != collection dimension {expected}; refusing ingest",
    )


def document_load_failed(path: str, reason: str) -> DomainError:
    """A single file could not be parsed; ingestion continues with the others."""
    return DomainError(
        code="knowledge.document_load_failed",
        message=f"Failed to load {path}: {reason}",
    )
