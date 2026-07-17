"""Conversation providers + the cross-module bridges (backend/README rule 4).

`conversation` may not import `knowledge` or `identity_access` (rule 3), so the
adapters that bridge them live here, at the composition root — the one place
allowed to know both vocabularies. Each bridge is a translation and nothing
more: no policy, no decisions, no "small fix" on the way through.
"""

from dataclasses import dataclass
from typing import Any

import structlog

from conversation.adapters.outbound.demo_customer_reference import DemoCustomerReferenceResolver
from conversation.adapters.outbound.llm.fallback import FallbackLlm
from conversation.adapters.outbound.llm.gemini import GeminiLlm
from conversation.adapters.outbound.llm.ollama import OllamaLlm
from conversation.adapters.outbound.llm.openrouter import OpenRouterLlm
from conversation.adapters.outbound.llm.traced import TracedLlm
from conversation.application.graph.builder import build_graph
from conversation.application.graph.dependencies import GraphConfig, GraphDependencies
from conversation.application.ports.authorization import AuthorizationOutcome, AuthorizationPort
from conversation.application.ports.banking_workflow import BankingWorkflowPort
from conversation.application.ports.llm import LlmPort
from conversation.application.ports.retrieval import RetrievalError
from conversation.domain.values import Citation, Evidence, ResourceRef, Retrieval
from identity_access.application.dto import AuthorizationRequest
from identity_access.application.use_cases.authorize_action import AuthorizeAction
from identity_access.domain.authorization import Action, AuthorizationService, Permit
from identity_access.domain.values import AuthenticatedUser
from knowledge.application.dto import RetrieveQuery
from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge
from knowledge.domain.retrieval import RetrievalOutcome
from shared.adapters.event_publisher import PostgresEventPublisher
from shared.adapters.langfuse_tracer import LangfuseTracer
from shared.adapters.noop_tracer import NoopTracer
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator
from shared.application.ports.tracer import TracerPort
from shared.config import Settings
from shared.domain.result import Err

logger = structlog.get_logger(__name__)


class KnowledgeRetrievalAdapter:
    """knowledge.RetrieveKnowledge → conversation.RetrievalPort.

    A typed knowledge failure becomes `RetrievalError`, never an empty
    retrieval: "nothing relevant" and "the KB is down" are different answers
    (rag.md §7), and only one of them is a refusal.
    """

    def __init__(self, use_case: RetrieveKnowledge) -> None:
        self._use_case = use_case

    async def retrieve(self, query: str, *, source_type: str | None = None) -> Retrieval:
        result = await self._use_case.execute(RetrieveQuery(text=query, source_type=None))
        if isinstance(result, Err):
            raise RetrievalError(result.error.message)
        return _to_retrieval(query, result.value)


def _to_retrieval(query: str, outcome: RetrievalOutcome) -> Retrieval:
    evidence = tuple(
        Evidence(
            text=scored.chunk.text,
            citation=Citation(
                document_id=scored.chunk.metadata.document_id,
                title=scored.chunk.metadata.title,
                section=scored.chunk.metadata.section,
                page=scored.chunk.metadata.page,
            ),
            score=scored.score,
        )
        for scored in outcome.chunks
    )
    return Retrieval(
        query=query,
        evidence=evidence,
        below_floor=outcome.below_floor,
        best_score=outcome.best_score,
    )


class IdentityAuthorizationAdapter:
    """identity_access.AuthorizeAction → conversation.AuthorizationPort.

    An action name the registry knows but identity doesn't is a wiring bug, and
    it denies — fail closed (ADR-011). The graph never sees a Permit it didn't
    earn from the matrix.
    """

    def __init__(self, use_case: AuthorizeAction) -> None:
        self._use_case = use_case

    async def authorize(
        self, *, user: object, action: str, resource: ResourceRef | None = None
    ) -> AuthorizationOutcome:
        if not isinstance(user, AuthenticatedUser):
            return AuthorizationOutcome(permitted=False, reason="malformed")
        try:
            action_value = Action(action)
        except ValueError:
            return AuthorizationOutcome(permitted=False, reason="internal_error")

        decision = await self._use_case.execute(
            AuthorizationRequest(user=user, action=action_value, resource=resource)
        )
        if isinstance(decision, Permit):
            return AuthorizationOutcome(permitted=True)
        return AuthorizationOutcome(permitted=False, reason=decision.reason.value)


def build_llm(settings: Settings, clock: Clock) -> LlmPort:
    """The shared, traced fallback chain — built once and reused by every
    consumer (graph nodes, confirmation classifier) so telemetry and failover
    state stay coherent."""
    # TracedLlm wraps the *chain*, so the generation reports the provider
    # that actually answered rather than the one we hoped would.
    return TracedLlm(_build_llm(settings, clock))


def _build_llm(settings: Settings, clock: Clock) -> LlmPort:
    """Fallback order comes from config; the configured primary always leads it,
    and unknown/unkeyed providers drop out rather than fail a turn later."""
    order = [p.strip() for p in settings.llm_fallback_order.split(",") if p.strip()]
    if settings.llm_provider in order:
        order.remove(settings.llm_provider)
    order.insert(0, settings.llm_provider)

    providers: list[LlmPort] = []
    for name in order:
        provider = _build_provider(name, settings)
        if provider is not None:
            providers.append(provider)
    if not providers:
        raise RuntimeError(
            "No LLM provider is usable — set GEMINI_API_KEY, OPENROUTER_API_KEY, or OLLAMA_URL"
        )
    return FallbackLlm(providers=providers, clock=clock)


def _build_provider(name: str, settings: Settings) -> LlmPort | None:
    if name == "gemini" and settings.gemini_api_key:
        return GeminiLlm(api_key=settings.gemini_api_key, model=settings.gemini_model)
    if name == "openrouter" and settings.openrouter_api_key:
        return OpenRouterLlm(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
        )
    if name == "ollama" and settings.ollama_url:
        return OllamaLlm(base_url=settings.ollama_url, model=settings.ollama_model)
    return None


def build_tracer(settings: Settings) -> TracerPort:
    """Langfuse when keyed, no-op otherwise (ADR-010).

    Unkeyed is a supported configuration, not a degraded one: evals and tests
    run this way by design (NFR-7). It is logged at wiring time so "where are my
    traces?" has an answer in the first ten lines of the boot log, rather than
    in a silence that looks like Langfuse being broken.
    """
    if not settings.tracing_enabled:
        logger.info("telemetry.tracer_disabled", reason="LANGFUSE_*_KEY not set")
        return NoopTracer()
    logger.info("telemetry.tracer_enabled", host=settings.langfuse_host)
    return LangfuseTracer(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.env,
    )


@dataclass(frozen=True)
class ConversationProviders:
    llm: LlmPort
    event_publisher: EventPublisher
    tracer: TracerPort
    banking: BankingWorkflowPort

    @classmethod
    def build(
        cls,
        settings: Settings,
        clock: Clock,
        banking: BankingWorkflowPort,
        llm: LlmPort | None = None,
    ) -> "ConversationProviders":
        return cls(
            llm=llm or build_llm(settings, clock),
            event_publisher=PostgresEventPublisher(),
            tracer=build_tracer(settings),
            banking=banking,
        )

    def graph_dependencies(
        self,
        *,
        settings: Settings,
        retrieve_use_case: RetrieveKnowledge,
        clock: Clock,
        id_generator: IdGenerator,
        authorization: AuthorizationPort | None = None,
    ) -> GraphDependencies:
        return GraphDependencies(
            llm=self.llm,
            retrieval=KnowledgeRetrievalAdapter(retrieve_use_case),
            authorization=authorization
            or IdentityAuthorizationAdapter(
                AuthorizeAction(
                    service=AuthorizationService(),
                    clock=clock,
                    id_generator=id_generator,
                    event_publisher=self.event_publisher,
                )
            ),
            customer_references=DemoCustomerReferenceResolver(),
            banking=self.banking,
            events=self.event_publisher,
            clock=clock,
            id_generator=id_generator,
            config=GraphConfig(
                history_window_turns=settings.agent_history_window_turns,
                max_input_chars=settings.agent_max_input_chars,
                answer_max_tokens=settings.agent_answer_max_tokens,
                generation_temperature=settings.agent_generation_temperature,
                grounding_judge_enabled=settings.agent_grounding_judge_enabled,
            ),
        )

    def build_graph(self, deps: GraphDependencies, checkpointer: object | None) -> Any:
        return build_graph(deps, checkpointer=checkpointer)
