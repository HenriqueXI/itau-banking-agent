"""Retrieve citation-ready chunks with a relevance floor (PRD002-FR-6, rag.md §3).

embed query → nearest top-k (optional source_type filter) → relevance floor
(below ⇒ refusal-path outcome, zero chunks) → dedupe near-identical → token cap.
Chroma/embedding failure ⇒ typed KnowledgeUnavailable (never silent).
"""

import structlog

from knowledge.application.dto import RetrieveQuery
from knowledge.application.ports.embedding import EmbeddingError, EmbeddingPort
from knowledge.application.ports.vector_store import VectorStoreError, VectorStorePort
from knowledge.domain.chunking import estimate_tokens
from knowledge.domain.errors import knowledge_unavailable
from knowledge.domain.retrieval import RetrievalOutcome
from knowledge.domain.values import ScoredChunk
from shared.domain.errors import DomainError
from shared.domain.result import Err, Ok, Result

logger = structlog.get_logger(__name__)


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class RetrieveKnowledge:
    def __init__(
        self,
        *,
        embedder: EmbeddingPort,
        store: VectorStorePort,
        top_k: int,
        relevance_floor: float,
        context_token_cap: int,
        dedupe_similarity: float,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._floor = relevance_floor
        self._token_cap = context_token_cap
        self._dedupe = dedupe_similarity

    async def execute(self, query: RetrieveQuery) -> Result[RetrievalOutcome, DomainError]:
        top_k = query.top_k or self._top_k
        try:
            embedding = await self._embedder.embed_query(query.text)
            hits = await self._store.query(embedding, top_k=top_k, source_type=query.source_type)
        except (EmbeddingError, VectorStoreError):
            logger.warning("kb.retrieve.unavailable", query=query.text)
            return Err(knowledge_unavailable())

        best = max((h.score for h in hits), default=None)
        if best is None or best < self._floor:
            logger.info("kb.retrieve.below_floor", query=query.text, best_score=best)
            return Ok(RetrievalOutcome.below_relevance_floor(query.text, best))

        kept = self._cap_tokens(self._dedupe_chunks(hits))
        logger.info(
            "kb.retrieve.grounded",
            query=query.text,
            best_score=best,
            returned=len(kept),
        )
        return Ok(RetrievalOutcome.grounded(query.text, tuple(kept)))

    def _dedupe_chunks(self, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        kept: list[ScoredChunk] = []
        for hit in hits:  # hits are sorted by score desc; keep the highest of near-dupes
            if any(_jaccard(hit.chunk.text, k.chunk.text) >= self._dedupe for k in kept):
                continue
            kept.append(hit)
        return kept

    def _cap_tokens(self, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        kept: list[ScoredChunk] = []
        total = 0
        for hit in hits:
            tokens = estimate_tokens(hit.chunk.text)
            if kept and total + tokens > self._token_cap:
                break
            kept.append(hit)
            total += tokens
        return kept
