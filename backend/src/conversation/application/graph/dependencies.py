"""Everything the nodes are allowed to touch, passed in explicitly.

Nodes are closures over this object rather than modules reaching for globals:
the graph is then trivially testable with fakes, and the dependency list doubles
as a review surface — if a node needs something that isn't here, that's a
conversation to have, not an import to add.
"""

from dataclasses import dataclass, field

from conversation.application.ports.authorization import AuthorizationPort
from conversation.application.ports.banking_workflow import BankingWorkflowPort
from conversation.application.ports.customer_reference import CustomerReferenceResolverPort
from conversation.application.ports.llm import LlmPort
from conversation.application.ports.retrieval import RetrievalPort
from conversation.application.prompts.library import PromptLibrary
from shared.application.ports.clock import Clock
from shared.application.ports.event_publisher import EventPublisher
from shared.application.ports.id_generator import IdGenerator


@dataclass(frozen=True, kw_only=True)
class GraphConfig:
    history_window_turns: int = 20
    max_input_chars: int = 4000
    answer_max_tokens: int = 512
    understand_max_tokens: int = 512
    # llm-providers.md §4: 0 for routing/judging, 0.3 for generation.
    extraction_temperature: float = 0.0
    generation_temperature: float = 0.3
    grounding_judge_enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class GraphDependencies:
    llm: LlmPort
    retrieval: RetrievalPort
    authorization: AuthorizationPort
    customer_references: CustomerReferenceResolverPort
    events: EventPublisher
    clock: Clock
    id_generator: IdGenerator
    prompts: PromptLibrary = field(default_factory=PromptLibrary)
    config: GraphConfig = GraphConfig()
    banking: BankingWorkflowPort | None = None
