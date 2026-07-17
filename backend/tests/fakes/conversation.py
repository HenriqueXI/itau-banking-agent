"""Deterministic conversation fakes (NFR-7): zero network, zero LLM quota.

`ScriptedLlm` answers by matching the prompt against rules the test declares, so
a graph test asserts routing and guardrails — not a model's mood. The rules are
matched in order and the last one wins as a default, which keeps tests that only
care about one node from having to script the others.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from conversation.application.ports.authorization import AuthorizationOutcome
from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    Usage,
)
from conversation.application.ports.retrieval import RetrievalError
from conversation.domain.values import (
    Citation,
    ConversationThread,
    Evidence,
    ResourceRef,
    Retrieval,
)
from shared.adapters.noop_tracer import NoopTracer


class FakeConversationProviders:
    """Stands in for api.ConversationProviders: the router only reads the event
    publisher and the tracer off it."""

    def __init__(
        self, event_publisher: Any, tracer: Any | None = None, banking: Any = None
    ) -> None:
        self.event_publisher = event_publisher
        self.tracer = tracer or NoopTracer()
        self.banking = banking


class ScriptedLlm:
    """Rule-based fake. `rules` maps a substring of the rendered prompt to the
    text to return; `default` answers anything unmatched."""

    def __init__(
        self,
        rules: list[tuple[str, str]] | None = None,
        *,
        default: str = "Resposta padrão.",
        provider: str = "fake",
        fail_with: LlmError | None = None,
    ) -> None:
        self._rules = rules or []
        self._default = default
        self._provider = provider
        self._fail_with = fail_with
        self.calls: list[list[LlmMessage]] = []

    @property
    def provider(self) -> str:
        return self._provider

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion:
        self.calls.append(messages)
        if self._fail_with is not None:
            raise self._fail_with
        return Completion(
            text=self._answer(messages),
            provider=self._provider,
            model="scripted",
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    async def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]:
        self.calls.append(messages)
        if self._fail_with is not None:
            raise self._fail_with
        for word in self._answer(messages).split(" "):
            yield Delta(text=word + " ", provider=self._provider, model="scripted")

    def _answer(self, messages: list[LlmMessage]) -> str:
        prompt = "\n".join(m.content for m in messages)
        for needle, answer in self._rules:
            if needle in prompt:
                return answer
        return self._default


class StubRetrieval:
    """Returns whatever the test declares. `error` simulates a KB outage — the
    port's contract distinguishes 'nothing relevant' from 'unreachable'."""

    def __init__(self, retrieval: Retrieval | None = None, *, error: bool = False) -> None:
        self._retrieval = retrieval
        self._error = error
        self.queries: list[str] = []

    async def retrieve(self, query: str, *, source_type: str | None = None) -> Retrieval:
        self.queries.append(query)
        if self._error:
            raise RetrievalError("chroma down")
        if self._retrieval is not None:
            return self._retrieval
        return Retrieval(query=query, evidence=(), below_floor=True, best_score=0.1)


class StubAuthorization:
    """Records what was asked, answers what the test set. A test that flips this
    to deny is asserting the graph's *topology*, not the matrix (that's
    identity_access's own exhaustive spec test)."""

    def __init__(self, *, permitted: bool = True, reason: str | None = None) -> None:
        self._permitted = permitted
        self._reason = reason
        self.requests: list[tuple[str, ResourceRef | None]] = []

    async def authorize(
        self, *, user: object, action: str, resource: ResourceRef | None = None
    ) -> AuthorizationOutcome:
        self.requests.append((action, resource))
        if self._permitted:
            return AuthorizationOutcome(permitted=True)
        return AuthorizationOutcome(permitted=False, reason=self._reason or "role_forbidden")


class InMemoryThreadRepository:
    def __init__(self) -> None:
        self._threads: dict[str, ConversationThread] = {}

    async def get(self, thread_id: str) -> ConversationThread | None:
        return self._threads.get(thread_id)

    async def claim(self, thread_id: str, user_id: uuid.UUID) -> ConversationThread:
        existing = self._threads.get(thread_id)
        if existing is not None:
            return existing
        thread = ConversationThread(thread_id=thread_id, user_id=user_id)
        self._threads[thread_id] = thread
        return thread

    async def list_for_user(self, user_id: uuid.UUID) -> list[ConversationThread]:
        return [t for t in self._threads.values() if t.user_id == user_id]


def evidence(
    text: str,
    *,
    title: str = "Tarifas 2026",
    section: str = "Consignado",
    page: int | None = None,
    score: float = 0.9,
) -> Evidence:
    return Evidence(
        text=text,
        citation=Citation(document_id="tarifas", title=title, section=section, page=page),
        score=score,
    )


def grounded(*items: Evidence, query: str = "pergunta") -> Retrieval:
    return Retrieval(
        query=query,
        evidence=items,
        below_floor=False,
        best_score=max(i.score for i in items),
    )


def understand_json(
    *,
    intent: str,
    tool: str | None = None,
    params: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    references_resolved: bool = False,
) -> str:
    import json

    return json.dumps(
        {
            "intent": intent,
            "tool": tool,
            "params": params or {},
            "target_resource": target,
            "references_resolved": references_resolved,
            "missing_param": None,
            "ambiguity": None,
        },
        ensure_ascii=False,
    )


def judge_json(grounded_verdict: bool) -> str:
    import json

    return json.dumps({"grounded": grounded_verdict, "unsupported": []})


ScriptRule = Callable[[str], bool]
