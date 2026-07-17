"""Knowledge providers wired at the composition root (backend/README rule 4).

Selects the embedding adapter per `settings.embedding_provider` (ADR-008) and
exposes factories for the ingest/retrieve use cases so both the API and the
`scripts/` entrypoints build them the same way.
"""

from dataclasses import dataclass

from knowledge.adapters.outbound.chroma.vector_store import ChromaVectorStore
from knowledge.adapters.outbound.embedding.gemini import GeminiEmbedder
from knowledge.adapters.outbound.embedding.ollama import OllamaEmbedder
from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
from knowledge.application.ports.document_loader import DocumentLoaderPort
from knowledge.application.ports.embedding import EmbeddingPort
from knowledge.application.ports.vector_store import VectorStorePort
from knowledge.application.use_cases.ingest_knowledge_base import IngestKnowledgeBase
from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge
from shared.adapters.event_publisher import PostgresEventPublisher
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.config import Settings


def _build_embedder(settings: Settings) -> EmbeddingPort:
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(base_url=settings.ollama_url, model=settings.ollama_embedding_model)
    return GeminiEmbedder(
        api_key=settings.gemini_api_key,
        model=settings.gemini_embedding_model,
        dimension=settings.gemini_embedding_dimension,
    )


@dataclass(frozen=True)
class KnowledgeProviders:
    loader: DocumentLoaderPort
    embedder: EmbeddingPort
    store: VectorStorePort
    event_publisher: EventPublisher

    @classmethod
    def build(cls, settings: Settings) -> "KnowledgeProviders":
        return cls(
            loader=LangChainDocumentLoader(),
            embedder=_build_embedder(settings),
            store=ChromaVectorStore(url=settings.chroma_url, collection=settings.chroma_collection),
            event_publisher=PostgresEventPublisher(),
        )

    def ingest_use_case(self, *, clock: Clock, id_generator: IdGenerator) -> IngestKnowledgeBase:
        return IngestKnowledgeBase(
            loader=self.loader,
            embedder=self.embedder,
            store=self.store,
            events=self.event_publisher,
            clock=clock,
            id_generator=id_generator,
        )

    def retrieve_use_case(self, settings: Settings) -> RetrieveKnowledge:
        return RetrieveKnowledge(
            embedder=self.embedder,
            store=self.store,
            top_k=settings.rag_top_k,
            relevance_floor=settings.rag_relevance_floor,
            context_token_cap=settings.rag_context_token_cap,
            dedupe_similarity=settings.rag_dedupe_similarity,
        )
