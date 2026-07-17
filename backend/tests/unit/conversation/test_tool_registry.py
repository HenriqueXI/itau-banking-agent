"""The tool registry IS the capability spec — read against it.

An unregistered capability must be impossible, not merely unlikely: that's what
the boot-time assertion buys.
"""

import pytest

from conversation.domain.tools import (
    REGISTRY,
    RiskTier,
    ToolSpec,
    assert_registry_complete,
    banking_tool_names,
    tool_for,
    tool_for_intent,
)
from conversation.domain.values import Intent


def test_registry_matches_the_agents_md_table() -> None:
    assert {name: (spec.action, int(spec.tier)) for name, spec in REGISTRY.items()} == {
        "buscar_conhecimento": ("kb_query", 1),
        "consultar_perfil": ("view_profile", 1),
        "consultar_limite": ("view_limit", 1),
        "consultar_saldo": ("view_balance", 1),
        "consultar_fatura": ("view_invoice", 1),
        "consultar_extrato": ("view_transactions", 1),
        "analisar_fatura": ("view_invoice", 1),
        "alterar_limite": ("update_card_limit", 2),
        "fazer_pix": ("create_pix", 3),
    }


def test_every_capability_intent_has_exactly_one_tool() -> None:
    assert_registry_complete()


def test_conversational_intents_reach_no_tool() -> None:
    """smalltalk/unclear have no capability — no tool, no action, no execution."""
    assert tool_for_intent(Intent.SMALLTALK) is None
    assert tool_for_intent(Intent.UNCLEAR) is None


def test_unknown_tool_name_resolves_to_nothing() -> None:
    assert tool_for("root_shell") is None
    assert tool_for(None) is None


def test_write_operations_are_tier_2_or_higher() -> None:
    """BR-4.1: a write can never be a tier-1 read, which is what would skip the
    confirmation gate."""
    for name in ("alterar_limite", "fazer_pix"):
        assert REGISTRY[name].tier >= RiskTier.WRITE_CONFIRM


def test_startup_rejects_a_write_tool_registered_as_tier_1() -> None:
    """PRD015-FR-3: a future write capability cannot skip graph gates."""
    invalid = dict(REGISTRY)
    current = REGISTRY["alterar_limite"]
    invalid["alterar_limite"] = ToolSpec(
        name=current.name,
        intent=current.intent,
        action=current.action,
        tier=RiskTier.READ,
        required_params=current.required_params,
        optional_params=current.optional_params,
        own_resource_only=current.own_resource_only,
        resource_kind=current.resource_kind,
    )

    with pytest.raises(RuntimeError, match="tier 2 or higher"):
        assert_registry_complete(invalid)


def test_pix_requires_step_up_tier() -> None:
    assert REGISTRY["fazer_pix"].tier is RiskTier.WRITE_STEP_UP


def test_banking_tools_exclude_the_knowledge_facade() -> None:
    assert "buscar_conhecimento" not in banking_tool_names()
    assert "fazer_pix" in banking_tool_names()


@pytest.mark.parametrize(
    ("tool", "params", "expected"),
    [
        ("fazer_pix", {}, ("amount", "pix_key")),
        ("fazer_pix", {"amount": 500}, ("pix_key",)),
        ("fazer_pix", {"amount": 500, "pix_key": "x"}, ()),
        ("alterar_limite", {"amount": None}, ("amount",)),
        ("consultar_limite", {}, ()),
    ],
)
def test_missing_params_come_from_the_schema(
    tool: str, params: dict[str, object], expected: tuple[str, ...]
) -> None:
    assert REGISTRY[tool].missing_params(params) == expected
