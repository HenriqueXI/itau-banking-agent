"""Tool registry: the agent's capabilities and their security metadata.

Registration is mandatory and complete — action name, risk tier (BR-4.1),
parameter schema, ownership semantics. A capability missing any of these fails
startup (`assert_registry_complete`); there are no unregistered tools.

The registry is data, not behavior: `gate` reads the tier from here, `authorize`
reads the action name from here. The LLM picks a *name*; what that name is
allowed to do is decided by this table plus code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum

from conversation.domain.values import Intent


class RiskTier(IntEnum):
    """BR-4.1 tiers: 1 read, 2 write-with-confirmation, 3 write-with-step-up."""

    READ = 1
    WRITE_CONFIRM = 2
    WRITE_STEP_UP = 3


@dataclass(frozen=True, kw_only=True)
class ToolSpec:
    """One agent-facing capability. `action` is the authorization action name
    (identity_access's `Action` values — matched by string at the port boundary,
    since modules don't import each other's internals).

    `own_resource_only` documents ownership semantics for the registration
    review; the enforcement itself is the authorization matrix, not this flag.
    """

    name: str
    intent: Intent
    action: str
    tier: RiskTier
    required_params: frozenset[str]
    optional_params: frozenset[str] = frozenset()
    own_resource_only: bool = True
    resource_kind: str | None = None

    @property
    def params(self) -> frozenset[str]:
        return self.required_params | self.optional_params

    def missing_params(self, provided: dict[str, object]) -> tuple[str, ...]:
        return tuple(sorted(p for p in self.required_params if provided.get(p) in (None, "", [])))


# Adding a row is an architecture review item.
REGISTRY: dict[str, ToolSpec] = {
    "buscar_conhecimento": ToolSpec(
        name="buscar_conhecimento",
        intent=Intent.KB_QUERY,
        action="kb_query",
        tier=RiskTier.READ,
        required_params=frozenset({"query"}),
        own_resource_only=False,
    ),
    "consultar_perfil": ToolSpec(
        name="consultar_perfil",
        intent=Intent.VIEW_PROFILE,
        action="view_profile",
        tier=RiskTier.READ,
        required_params=frozenset(),
        resource_kind="customer",
    ),
    "consultar_limite": ToolSpec(
        name="consultar_limite",
        intent=Intent.VIEW_LIMIT,
        action="view_limit",
        tier=RiskTier.READ,
        required_params=frozenset(),
        optional_params=frozenset({"card_id"}),
        resource_kind="card",
    ),
    "consultar_saldo": ToolSpec(
        name="consultar_saldo",
        intent=Intent.VIEW_BALANCE,
        action="view_balance",
        tier=RiskTier.READ,
        required_params=frozenset(),
        optional_params=frozenset({"account_id"}),
        resource_kind="account",
    ),
    "consultar_fatura": ToolSpec(
        name="consultar_fatura",
        intent=Intent.VIEW_INVOICE,
        action="view_invoice",
        tier=RiskTier.READ,
        required_params=frozenset(),
        optional_params=frozenset({"card_id"}),
        resource_kind="card",
    ),
    "consultar_extrato": ToolSpec(
        name="consultar_extrato",
        intent=Intent.VIEW_TRANSACTIONS,
        action="view_transactions",
        tier=RiskTier.READ,
        required_params=frozenset(),
        optional_params=frozenset({"account_id"}),
        resource_kind="account",
    ),
    "analisar_fatura": ToolSpec(
        name="analisar_fatura",
        intent=Intent.HYBRID_INVOICE_GUIDANCE,
        action="view_invoice",
        tier=RiskTier.READ,
        required_params=frozenset(),
        optional_params=frozenset({"card_id", "account_id", "query"}),
        resource_kind="card",
    ),
    "alterar_limite": ToolSpec(
        name="alterar_limite",
        intent=Intent.UPDATE_CARD_LIMIT,
        action="update_card_limit",
        tier=RiskTier.WRITE_CONFIRM,
        required_params=frozenset({"amount"}),
        optional_params=frozenset({"card_id"}),
        resource_kind="card",
    ),
    "fazer_pix": ToolSpec(
        name="fazer_pix",
        intent=Intent.CREATE_PIX,
        action="create_pix",
        tier=RiskTier.WRITE_STEP_UP,
        required_params=frozenset({"amount", "pix_key"}),
        optional_params=frozenset({"recipient_name", "description"}),
        resource_kind="account",
    ),
}

_INTENT_TO_TOOL = {spec.intent: spec for spec in REGISTRY.values()}

# Intents that reach no capability: no tool, no authorization, no execution.
CONVERSATIONAL_INTENTS = frozenset({Intent.SMALLTALK, Intent.UNCLEAR})


def tool_for(name: str | None) -> ToolSpec | None:
    return REGISTRY.get(name) if name else None


def tool_for_intent(intent: Intent) -> ToolSpec | None:
    return _INTENT_TO_TOOL.get(intent)


def banking_tool_names() -> tuple[str, ...]:
    """Everything except the knowledge façade — the operation_flow surface."""
    return tuple(sorted(n for n, s in REGISTRY.items() if s.intent is not Intent.KB_QUERY))


def assert_registry_complete(registry: Mapping[str, ToolSpec] | None = None) -> None:
    """Startup guard: every intent that names a capability has a
    registered tool, and every registration carries its security metadata.

    ``registry`` is injectable for the structural security test.  Startup uses
    the canonical registry; the injected form proves a future write tool cannot
    silently be assigned tier 1 and bypass the graph gates.
    """
    registry = REGISTRY if registry is None else registry
    intent_to_tool = {spec.intent: spec for spec in registry.values()}
    unregistered = [
        intent
        for intent in Intent
        if intent not in CONVERSATIONAL_INTENTS and intent not in intent_to_tool
    ]
    if unregistered:
        raise RuntimeError(f"Tool registry incomplete — no tool for intents {unregistered}")

    write_actions = frozenset({"update_card_limit", "create_pix"})
    for spec in registry.values():
        if not spec.action or not isinstance(spec.tier, RiskTier):
            raise RuntimeError(f"Tool {spec.name!r} lacks complete registration (action/tier)")
        if spec.action in write_actions and spec.tier < RiskTier.WRITE_CONFIRM:
            raise RuntimeError(f"Write tool {spec.name!r} must have tier 2 or higher")
        if spec.own_resource_only and spec.resource_kind is None and spec.tier is not RiskTier.READ:
            raise RuntimeError(f"Tool {spec.name!r} is ownership-scoped but names no resource kind")

    if len(intent_to_tool) != len(registry):
        raise RuntimeError(
            "Tool registry maps two tools to one intent — routing would be ambiguous"
        )
