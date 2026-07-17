"""`understand` output parsing: the boundary where model text becomes a claim.

These tests are the spec for what the graph will and won't accept from an LLM.
The rule they encode: unrecognized = dropped, never passed through.
"""

from decimal import Decimal

import pytest

from conversation.application.graph.nodes.understand_node import (
    drop_unsupported_params,
    normalize_amount,
    parse_understanding,
)
from conversation.application.json_repair import parse_json_object
from conversation.domain.values import Intent, Understanding


def test_parses_a_clean_extraction() -> None:
    understanding = parse_understanding(
        '{"intent": "kb_query", "tool": "buscar_conhecimento",'
        ' "params": {"query": "taxa do consignado"}}'
    )
    assert understanding is not None
    assert understanding.intent is Intent.KB_QUERY
    assert understanding.tool == "buscar_conhecimento"
    assert understanding.params == {"query": "taxa do consignado"}


def test_unknown_intent_is_rejected_rather_than_coerced() -> None:
    """A model inventing `admin_override` produces nothing usable — there is no
    path from an unregistered word to a capability."""
    assert parse_understanding('{"intent": "admin_override", "tool": "fazer_pix"}') is None


def test_unregistered_tool_falls_back_to_the_intent_registry() -> None:
    understanding = parse_understanding('{"intent": "view_limit", "tool": "consultar_tudo"}')
    assert understanding is not None
    assert understanding.tool == "consultar_limite"


def test_params_outside_the_tool_schema_are_dropped() -> None:
    understanding = parse_understanding(
        '{"intent": "update_card_limit", "tool": "alterar_limite",'
        ' "params": {"amount": 10000, "bypass_confirmation": true}}'
    )
    assert understanding is not None
    assert "bypass_confirmation" not in understanding.params


def test_missing_required_param_is_computed_from_the_schema_not_the_model() -> None:
    understanding = parse_understanding(
        '{"intent": "create_pix", "tool": "fazer_pix", "params": {"amount": 500},'
        ' "missing_param": null}'
    )
    assert understanding is not None
    assert understanding.missing_param == "pix_key"
    assert understanding.needs_clarification


def test_third_party_target_is_preserved_verbatim() -> None:
    understanding = parse_understanding(
        '{"intent": "view_balance", "tool": "consultar_saldo",'
        ' "target_resource": {"kind": "account", "owner_id": "João Silva"}}'
    )
    assert understanding is not None
    assert understanding.target_resource is not None
    assert understanding.target_resource.owner_id == "João Silva"


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"intent": "smalltalk"}\n```',
        'Claro! Aqui está: {"intent": "smalltalk"}',
        '{"intent": "smalltalk",}',
        '{"intent": "smalltalk"} Espero ter ajudado.',
    ],
)
def test_repairs_common_free_model_json_deviations(raw: str) -> None:
    understanding = parse_understanding(raw)
    assert understanding is not None
    assert understanding.intent is Intent.SMALLTALK


def test_unparseable_output_returns_none_so_the_caller_can_clarify() -> None:
    assert parse_understanding("Desculpe, não entendi.") is None


def test_json_repair_survives_braces_inside_strings() -> None:
    payload = parse_json_object('{"intent": "smalltalk", "note": "um { aqui"}')
    assert payload == {"intent": "smalltalk", "note": "um { aqui"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (10000, Decimal(10000)),
        ("10000", Decimal(10000)),
        ("10 mil", Decimal(10000)),
        ("R$ 10.000,00", Decimal("10000.00")),
        ("dez mil reais", Decimal(10000)),
        ("R$ 1.500,50", Decimal("1500.50")),
        ("500", Decimal(500)),
        ("quinhentos", Decimal(500)),
        ("2 milhões", Decimal(2_000_000)),
    ],
)
def test_amount_formats_normalize_to_decimal(raw: object, expected: Decimal) -> None:
    """Money is Decimal, never float — including when it arrives as prose."""
    normalized = normalize_amount(raw)
    assert isinstance(normalized, Decimal)
    assert normalized == expected


def test_unrecognized_amount_passes_through_for_honest_rejection() -> None:
    """Better a validation error downstream than a silent zero."""
    assert normalize_amount("um valor qualquer") == "um valor qualquer"


class TestInventedParams:
    """The model may not introduce a value the conversation never contained.

    Prompts ask it not to; `drop_unsupported_params` makes it impossible — a
    few-shot example's PIX key must never become a real transfer's destination.
    """

    def _pix(self, **params: object) -> Understanding:
        return Understanding(intent=Intent.CREATE_PIX, tool="fazer_pix", params=dict(params))

    def test_pix_key_absent_from_the_conversation_is_dropped(self) -> None:
        cleaned = drop_unsupported_params(
            self._pix(amount=500, pix_key="irmao@email.com"),
            message="manda um pix de 500 pro meu irmão",
            history="",
        )
        assert "pix_key" not in cleaned.params
        assert cleaned.params["amount"] == 500
        assert cleaned.missing_param == "pix_key"  # → clarify, not a guess

    def test_pix_key_from_history_survives(self) -> None:
        cleaned = drop_unsupported_params(
            self._pix(amount=200, pix_key="irmao@email.com"),
            message="manda 200 pro meu irmão",
            history="Usuário: a chave pix do meu irmão é irmao@email.com",
        )
        assert cleaned.params["pix_key"] == "irmao@email.com"

    def test_punctuation_differences_still_count_as_mentioned(self) -> None:
        cleaned = drop_unsupported_params(
            self._pix(amount=200, pix_key="12345678900"),
            message="manda 200 pro cpf 123.456.789-00",
            history="",
        )
        assert cleaned.params["pix_key"] == "12345678900"

    def test_invented_amount_is_dropped(self) -> None:
        cleaned = drop_unsupported_params(
            Understanding(
                intent=Intent.UPDATE_CARD_LIMIT,
                tool="alterar_limite",
                params={"amount": Decimal(5000)},
            ),
            message="aumenta o limite",
            history="",
        )
        assert cleaned.params == {}
        assert cleaned.missing_param == "amount"

    @pytest.mark.parametrize(
        ("message", "amount"),
        [
            ("aumenta para 10 mil", Decimal(10000)),
            ("aumenta para R$ 10.000,00", Decimal(10000)),
            ("aumenta para dez mil reais", Decimal(10000)),
            ("quero 2500 de limite", Decimal(2500)),
        ],
    )
    def test_amounts_the_user_stated_survive_in_any_format(
        self, message: str, amount: Decimal
    ) -> None:
        cleaned = drop_unsupported_params(
            Understanding(
                intent=Intent.UPDATE_CARD_LIMIT, tool="alterar_limite", params={"amount": amount}
            ),
            message=message,
            history="",
        )
        assert cleaned.params["amount"] == amount

    def test_kb_query_is_exempt_because_it_is_a_rewrite(self) -> None:
        cleaned = drop_unsupported_params(
            Understanding(
                intent=Intent.KB_QUERY,
                tool="buscar_conhecimento",
                params={"query": "taxa do empréstimo consignado para aposentados"},
            ),
            message="e para aposentados?",
            history="Usuário: qual a taxa do consignado?",
        )
        assert cleaned.params["query"]
