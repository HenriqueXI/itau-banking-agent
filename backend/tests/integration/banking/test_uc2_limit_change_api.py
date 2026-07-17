"""UC-2 end-to-end over /api/agui: limit change with confirmation, executed
through the real MCP protocol against the injected simulator (PRD-007)."""

import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from banking.adapters.outbound.postgres.pending_operation_repository import (
    PostgresPendingOperationRepository,
)
from banking.adapters.outbound.postgres.tables import pending_operations
from banking.domain.pending_operation import PendingOperation
from mcp_server.simulator import CoreBankingSimulator
from shared.container import Container
from tests.fakes.conversation import ScriptedLlm, understand_json
from tests.integration.banking.conftest import (
    ANA_ID,
    BRUNO_ID,
    McpServerHandle,
    audit_rows,
    deliver_outbox,
    event_payload,
    login,
    resume_input,
    run_input,
    stream,
    stream_text,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def scripted_llm() -> ScriptedLlm:
    def limit_change(amount: str) -> str:
        # The panel summary only describes the authenticated user's own cards, so
        # a manager acting on a customer must name the card by its masked final
        # four — never by a panel-selected id (resolve_resource ignores that hint
        # for a third party). Ana owns two cards (4242, 8888); 4242 is card-1.
        return understand_json(
            intent="update_card_limit",
            tool="alterar_limite",
            params={"amount": amount},
            target={"kind": "card", "owner_id": "123", "id": "4242"},
        )

    return ScriptedLlm(
        [
            ("na verdade", limit_change("12000")),
            ("60.000", limit_change("60000")),
            ("15.050", limit_change("15050")),
            ("15.000", limit_change("15000")),
        ],
        default="Posso ajudar com assuntos do banco.",
    )


async def _operation_status(container: Container, operation_hash: str) -> tuple[str, str | None]:
    async with container.engine.connect() as connection:
        row = (
            await connection.execute(
                select(pending_operations).where(
                    pending_operations.c.operation_hash == operation_hash
                )
            )
        ).one()
    return row.status, row.cancellation_reason


@pytest.mark.adversarial
async def test_uc2_manager_limit_change_end_to_end(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """PRD-007 acceptance (UC-2): fresh MCP reads → eligibility → confirmation
    card 5.000→15.000 → confirm → update_card_limit once → audit row."""
    token = await login(client, "bruno-banking@demo")
    turn = await stream(
        client,
        token,
        run_input(
            "t-uc2-e2e",
            "Aumenta o limite do cartão final 4242 da Ana para R$ 15.000",
        ),
    )

    card = event_payload(turn, "confirmation_required")
    assert card is not None
    assert card["operation"] == "alterar_limite"
    assert Decimal(card["currentAmount"]) == Decimal("5000")
    assert Decimal(card["requestedAmount"]) == Decimal("15000")
    assert card["issuedAt"] < card["expiresAt"]
    assert [entry.tool for entry in simulator.call_log] == [
        "get_customer_profile",
        "get_customer_profile",
        "get_card_limit",
    ]

    receipt = await stream(
        client, token, resume_input("t-uc2-e2e", card["operationHash"], "confirmo")
    )
    text = stream_text(receipt)
    assert "4242" in text
    assert "R$ 5.000,00" in text
    assert "R$ 15.000,00" in text
    assert simulator.limit_updates == 1
    assert simulator.call_log[-1].requested_by == f"user:{BRUNO_ID}"

    await deliver_outbox(container)
    changed = await audit_rows(
        container, action="CARD_LIMIT_CHANGE", outcome="changed", user_ref=str(BRUNO_ID)
    )
    assert any(row.amount == Decimal("15000") and row.trace_id is not None for row in changed)
    requested = await audit_rows(
        container, action="CARD_LIMIT_CHANGE", outcome="requested", user_ref=str(BRUNO_ID)
    )
    assert requested

    # Duplicate confirm (replay) must not execute a second change.
    replay = await stream(
        client, token, resume_input("t-uc2-e2e", card["operationHash"], "confirmo")
    )
    assert "Não há nenhuma operação aguardando confirmação" in stream_text(replay)
    assert simulator.limit_updates == 1


async def test_uc2_denial_states_maximum_and_never_calls_a_write_tool(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """US-3.2: above the BR-2.2 maximum → denial names the maximum, zero write
    calls, denial audited."""
    token = await login(client, "bruno-banking@demo")
    turn = await stream(
        client,
        token,
        run_input(
            "t-uc2-denial",
            "Aumenta o limite do cartão final 4242 da Ana para R$ 60.000",
        ),
    )

    assert "R$ 50.000,00" in stream_text(turn)
    assert event_payload(turn, "confirmation_required") is None
    assert all(entry.tool != "update_card_limit" for entry in simulator.call_log)

    await deliver_outbox(container)
    denied = await audit_rows(
        container, action="CARD_LIMIT_CHANGE", outcome="denied", user_ref=str(BRUNO_ID)
    )
    assert any(row.amount == Decimal("60000") for row in denied)


async def test_uc2_denial_for_non_multiple_names_the_correct_rule(
    client: httpx.AsyncClient,
    simulator: CoreBankingSimulator,
) -> None:
    """A limit that is below the maximum but not a multiple of R$ 100 must
    not be narrated as a maximum-limit denial."""
    token = await login(client, "bruno-banking@demo")
    turn = await stream(
        client,
        token,
        run_input(
            "t-uc2-multiple-denial",
            "Aumenta o limite do cartão final 4242 da Ana para R$ 15.050",
        ),
    )

    text = stream_text(turn)
    assert "múltiplo de R$ 100,00" in text or "multiplo de R$ 100,00" in text
    assert "R$ 50.000,00" not in text
    assert event_payload(turn, "confirmation_required") is None
    assert all(entry.tool != "update_card_limit" for entry in simulator.call_log)


@pytest.mark.adversarial
async def test_customer_limit_change_is_denied_before_any_mcp_call_and_audited(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """Challenge RBAC: Ana cannot change even her own card limit."""
    token = await login(client)
    turn = await stream(
        client,
        token,
        run_input(
            "t-uc2-customer-denied",
            "Aumenta meu limite para R$ 15.000",
            context={"selected_card_id": "card-1"},
        ),
    )

    denial = stream_text(turn)
    assert "não pode alterá-lo" in denial
    assert "manager e admin" in denial
    assert event_payload(turn, "confirmation_required") is None
    assert simulator.call_log == []

    await deliver_outbox(container)
    denied = await audit_rows(
        container, action="AUTHORIZATION_DENIED", outcome="denied", user_ref=str(ANA_ID)
    )
    assert any(row.details["attempted_action"] == "update_card_limit" for row in denied)


@pytest.mark.adversarial
async def test_customer_cannot_confirm_a_pending_limit_change_created_before_policy_change(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """Resume reauthorizes against the JWT before a legacy pending write."""
    operation = PendingOperation.create(
        operation_id=uuid.uuid4(),
        user_id=ANA_ID,
        tool="alterar_limite",
        params={
            "customer_id": "123",
            "card_id": "card-1",
            "current_limit": "5000",
            "new_limit": "15000",
            "last4": "4242",
        },
        tier=2,
        now=container.clock.now(),
        ttl=timedelta(minutes=5),
    )
    async with container.session_factory() as session, session.begin():
        await PostgresPendingOperationRepository(session).add(operation)

    token = await login(client)
    resumed = await stream(
        client, token, resume_input("t-uc2-customer-resume", operation.operation_hash, "confirmo")
    )

    denial = stream_text(resumed)
    assert "não pode alterá-lo" in denial
    assert "manager e admin" in denial
    assert simulator.limit_updates == 0
    status, reason = await _operation_status(container, operation.operation_hash)
    assert status == "cancelled"
    assert reason == "authorization_revoked"

    await deliver_outbox(container)
    denied = await audit_rows(
        container, action="AUTHORIZATION_DENIED", outcome="denied", user_ref=str(ANA_ID)
    )
    assert any(row.details["attempted_action"] == "update_card_limit" for row in denied)
    cancelled = await audit_rows(
        container, action="OPERATION_CONFIRMATION", outcome="cancelled", user_ref=str(ANA_ID)
    )
    assert any(row.resource == f"operation:{operation.operation_hash}" for row in cancelled)


@pytest.mark.adversarial
async def test_uc2_changed_params_cancel_the_pending_op_and_restart(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """Gherkin: pending 15.000, "na verdade 12 mil" → params_changed
    cancellation + a new confirmation for 12.000."""
    token = await login(client, "bruno-banking@demo")
    first = await stream(
        client,
        token,
        run_input(
            "t-uc2-params",
            "Aumenta o limite do cartão final 4242 da Ana para R$ 15.000",
        ),
    )
    first_card = event_payload(first, "confirmation_required")
    assert first_card is not None

    second = await stream(
        client,
        token,
        run_input(
            "t-uc2-params",
            "na verdade, 12 mil",
        ),
    )
    second_card = event_payload(second, "confirmation_required")
    assert second_card is not None
    assert Decimal(second_card["requestedAmount"]) == Decimal("12000")
    assert second_card["operationHash"] != first_card["operationHash"]

    status, reason = await _operation_status(container, first_card["operationHash"])
    assert status == "cancelled"
    assert reason == "params_changed"

    await deliver_outbox(container)
    cancelled = await audit_rows(container, action="OPERATION_CONFIRMATION", outcome="cancelled")
    assert any(row.resource == f"operation:{first_card['operationHash']}" for row in cancelled)

    # Resolve the new pending op so later tests start clean.
    await stream(
        client, token, resume_input("t-uc2-params", second_card["operationHash"], "confirmo")
    )
    assert simulator.limit_updates == 1


async def test_uc2_mcp_failure_after_confirm_is_failed_honest_and_audited(
    client: httpx.AsyncClient,
    container: Container,
    mcp_server: McpServerHandle,
) -> None:
    """Edge case: SystemUnavailable at execute → FAILED persisted, honest
    narration (nothing executed), failure audited — not a rolled-back mystery."""
    token = await login(client, "bruno-banking@demo")
    turn = await stream(
        client,
        token,
        run_input(
            "t-uc2-down",
            "Aumenta o limite do cartão final 4242 da Ana para R$ 15.000",
        ),
    )
    card = event_payload(turn, "confirmation_required")
    assert card is not None

    mcp_server.stop()
    resumed = await stream(
        client, token, resume_input("t-uc2-down", card["operationHash"], "confirmo")
    )
    text = stream_text(resumed)
    assert "Nao foi possivel concluir a alteracao de limite" in text
    assert "atualizado" not in text

    status, _ = await _operation_status(container, card["operationHash"])
    assert status == "failed"

    await deliver_outbox(container)
    failed = await audit_rows(
        container, action="CARD_LIMIT_CHANGE", outcome="failed", user_ref=str(BRUNO_ID)
    )
    assert any(row.details.get("operation_hash") == card["operationHash"] for row in failed)


@pytest.mark.adversarial
async def test_uc2_expired_confirmation_gets_cancelled_messaging(
    client: httpx.AsyncClient,
    container: Container,
    simulator: CoreBankingSimulator,
) -> None:
    """Edge case: confirming an operation past its TTL expires it — no MCP
    write, expiry persisted and audited."""
    now = container.clock.now()
    operation = PendingOperation.create(
        operation_id=uuid.uuid4(),
        user_id=ANA_ID,
        tool="alterar_limite",
        params={
            "customer_id": "123",
            "card_id": "card-1",
            "current_limit": "5000",
            "new_limit": "15000",
            "last4": "4242",
        },
        tier=2,
        now=now - timedelta(minutes=10),
        ttl=timedelta(minutes=5),
    )
    async with container.session_factory() as session, session.begin():
        await PostgresPendingOperationRepository(session).add(operation)

    token = await login(client)
    resumed = await stream(
        client, token, resume_input("t-uc2-expired", operation.operation_hash, "confirmo")
    )
    assert "Não há nenhuma operação aguardando confirmação" in stream_text(resumed)
    assert simulator.limit_updates == 0

    status, _ = await _operation_status(container, operation.operation_hash)
    assert status == "expired"

    await deliver_outbox(container)
    expired = await audit_rows(container, action="OPERATION_CONFIRMATION", outcome="expired")
    assert any(row.resource == f"operation:{operation.operation_hash}" for row in expired)
