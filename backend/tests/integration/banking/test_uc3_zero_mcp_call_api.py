"""UC-3 over /api/agui: third-party data request is denied *before* any MCP
call — the simulator call log is the proof (PRD007-FR-9)."""

import httpx
import pytest

from mcp_server.simulator import CoreBankingSimulator
from shared.container import Container
from tests.fakes.conversation import ScriptedLlm, understand_json
from tests.integration.banking.conftest import (
    ANA_ID,
    audit_rows,
    deliver_outbox,
    login,
    run_input,
    stream,
    stream_text,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def scripted_llm() -> ScriptedLlm:
    return ScriptedLlm(
        [
            (
                "Ana",
                understand_json(
                    intent="view_balance",
                    tool="consultar_saldo",
                    target={"kind": "account", "owner_id": "Ana"},
                ),
            ),
            (
                "João",
                understand_json(
                    intent="view_balance",
                    tool="consultar_saldo",
                    target={"kind": "account", "owner_id": "456"},
                ),
            ),
            (
                "Bruno",
                understand_json(
                    intent="view_balance",
                    tool="consultar_saldo",
                    target={"kind": "account", "owner_id": "Bruno"},
                ),
            ),
        ],
        default="Posso ajudar com assuntos do banco.",
    )


@pytest.mark.adversarial
async def test_uc3_deny_happens_before_any_mcp_call(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """PRD-007 acceptance (UC-3): the MCP call log stays empty for the turn,
    the refusal never confirms João exists, and the denial is audited."""
    token = await login(client)
    turn = await stream(client, token, run_input("t-uc3", "Qual o saldo do João Silva?"))

    text = stream_text(turn)
    assert "permissão" in text or "permissões" in text
    assert "João" not in text
    assert "456" not in text
    assert "erro" not in text.lower()

    assert simulator.call_log == []

    await deliver_outbox(container)
    denied = await audit_rows(
        container, action="AUTHORIZATION_DENIED", outcome="denied", user_ref=str(ANA_ID)
    )
    assert any(row.details.get("attempted_action") == "view_balance" for row in denied)


async def test_manager_reads_named_demo_customer_balance_via_mcp(
    client: httpx.AsyncClient,
    simulator: CoreBankingSimulator,
) -> None:
    """A manager's natural-language target resolves to Ana's canonical id first."""
    token = await login(client, "bruno-banking@demo")
    turn = await stream(client, token, run_input("t-manager-ana", "Pode consultar o saldo da Ana?"))

    text = stream_text(turn)
    assert "R$ 28.412,37" in text
    assert [call.tool for call in simulator.call_log] == [
        "get_customer_profile",
        "get_account_balance",
    ]


async def test_customer_named_third_party_is_denied_before_any_mcp_call(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    token = await login(client)
    turn = await stream(
        client, token, run_input("t-customer-bruno", "Pode consultar o saldo do Bruno?")
    )

    assert "permiss" in stream_text(turn).lower()
    assert simulator.call_log == []

    await deliver_outbox(container)
    denied = await audit_rows(
        container, action="AUTHORIZATION_DENIED", outcome="denied", user_ref=str(ANA_ID)
    )
    assert any(row.details.get("attempted_action") == "view_balance" for row in denied)
