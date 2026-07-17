"""RetrievalPort: the knowledge module as this module sees it.

Modules never import each other's internals (backend/README rule 3) — the
composition root binds this to `knowledge.RetrieveKnowledge` and translates
shapes. The port speaks `conversation.domain` vocabulary only.
"""

from typing import Protocol

from conversation.domain.values import Retrieval


class RetrievalError(Exception):
    """Knowledge base unreachable. Never a silent empty result: an empty
    retrieval means 'nothing relevant', this means 'we don't know' (rag.md §7)."""


class RetrievalPort(Protocol):
    async def retrieve(self, query: str, *, source_type: str | None = None) -> Retrieval: ...
