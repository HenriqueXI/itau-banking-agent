"""Retrieval outcome: the value the retrieve use case returns (rag.md §3).

A retrieval either clears the relevance floor (grounded evidence for generation)
or falls below it (refusal-path input, BR-8.3). Below-floor carries zero chunks
as evidence — the refusal must not leak partial matches.
"""

from dataclasses import dataclass

from knowledge.domain.values import Citation, ScoredChunk


@dataclass(frozen=True, kw_only=True)
class RetrievalOutcome:
    query: str
    chunks: tuple[ScoredChunk, ...]
    below_floor: bool
    best_score: float | None

    @property
    def citations(self) -> tuple[Citation, ...]:
        return tuple(sc.citation() for sc in self.chunks)

    @classmethod
    def grounded(cls, query: str, chunks: tuple[ScoredChunk, ...]) -> "RetrievalOutcome":
        best = max((c.score for c in chunks), default=None)
        return cls(query=query, chunks=chunks, below_floor=False, best_score=best)

    @classmethod
    def below_relevance_floor(cls, query: str, best_score: float | None) -> "RetrievalOutcome":
        return cls(query=query, chunks=(), below_floor=True, best_score=best_score)
