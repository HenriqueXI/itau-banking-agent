"""Composition for the MCP banking adapter (PRD-003)."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import cast

from banking.adapters.outbound.mcp_client import McpBankingSystemsClient
from banking.adapters.outbound.postgres.pending_operation_repository import (
    PostgresPendingOperationRepository,
    PostgresPixTransferRepository,
)
from banking.application.confirmation import ConfirmationDecision, InterpretConfirmation
from banking.application.dto import (
    LimitChangeConfirmation,
    LimitChangeRejected,
    PixTransferRejected,
    RequestCardLimitChange,
    RequestPixTransfer,
)
from banking.application.ports.confirmation_classifier import ConfirmationClassifierPort
from banking.application.use_cases.execute_card_limit_change import ExecuteCardLimitChangeUseCase
from banking.application.use_cases.execute_pix_transfer import ExecutePixTransferUseCase
from banking.application.use_cases.request_card_limit_change import RequestCardLimitChangeUseCase
from banking.application.use_cases.request_pix_transfer import RequestPixTransferUseCase
from banking.domain.eligibility import EligibilityPolicy
from banking.domain.errors import BankingSystemError
from banking.domain.pending_operation import OperationStatus
from conversation.application.json_repair import parse_json_object
from conversation.application.ports.authorization import AuthorizationPort
from conversation.application.ports.banking_workflow import (
    BalanceView,
    BankingWorkflowPort,
    CardReference,
    ConfirmationView,
    InvoiceView,
    LimitAuthorizationDeniedView,
    LimitConfirmationView,
    LimitReceiptView,
    LimitRejectedView,
    LimitRequestView,
    LimitView,
    OperationFailedView,
    PixConfirmationView,
    PixReceiptView,
    PixRejectedView,
    PixStepUpView,
    ProfileView,
    StatementView,
)
from conversation.application.ports.llm import LlmMessage, LlmPort, MessageRole
from conversation.application.prompts import library
from conversation.application.prompts.library import PromptLibrary
from conversation.domain.values import ResourceRef
from identity_access.adapters.outbound.postgres.step_up_repository import (
    PostgresStepUpChallengeRepository,
)
from identity_access.application.dto import VerifyStepUpCommand
from identity_access.application.use_cases.verify_step_up import VerifyStepUp
from identity_access.domain.values import AuthenticatedUser
from shared.adapters.event_publisher import PostgresEventPublisher, current_event_session
from shared.application.ports.clock import Clock
from shared.application.ports.id_generator import IdGenerator
from shared.config import Settings
from shared.domain.result import Err
from shared.logging.masking import mask_pii

_CONFIRM_INTENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": [d.value for d in ConfirmationDecision]}},
    "required": ["decision"],
}


class LlmConfirmationClassifier:
    """conversation.LlmPort → banking.ConfirmationClassifierPort (PRD007-FR-5).

    Classification only: the output is a label the state machine still gates.
    Anything that isn't a clean confirm/cancel — parse failure, unknown label,
    provider error — surfaces as AMBIGUOUS upstream (fail closed, re-ask).
    """

    def __init__(self, llm: LlmPort, prompts: PromptLibrary | None = None) -> None:
        self._llm = llm
        self._prompts = prompts or PromptLibrary()

    async def classify(self, response: str) -> ConfirmationDecision:
        prompt = self._prompts.render(library.CONFIRM_INTENT, response=response)
        completion = await self._llm.complete(
            [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)],
            json_schema=_CONFIRM_INTENT_SCHEMA,
            temperature=0.0,
            max_tokens=64,
        )
        payload = parse_json_object(completion.text)
        if payload is None:
            return ConfirmationDecision.AMBIGUOUS
        try:
            return ConfirmationDecision(str(payload.get("decision", "")).strip().lower())
        except ValueError:
            return ConfirmationDecision.AMBIGUOUS


@dataclass(frozen=True)
class BankingProviders:
    client: McpBankingSystemsClient

    @classmethod
    def build(cls, settings: Settings) -> "BankingProviders":
        return cls(client=McpBankingSystemsClient(url=settings.mcp_server_url))

    async def aclose(self) -> None:
        await self.client.aclose()


class BankingWorkflowAdapter(BankingWorkflowPort):
    """Composition-root bridge from the graph vocabulary to banking use cases."""

    def __init__(
        self,
        *,
        client: McpBankingSystemsClient,
        settings: Settings,
        clock: Clock,
        id_generator: IdGenerator,
        authorization: AuthorizationPort,
        confirmation_classifier: ConfirmationClassifierPort | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._ids = id_generator
        self._authorization = authorization
        self._events = PostgresEventPublisher()
        self._eligibility = EligibilityPolicy(maximums=settings.card_limit_maximums)
        self._interpret_confirmation = InterpretConfirmation(confirmation_classifier)

    def _operations(self) -> PostgresPendingOperationRepository:
        return PostgresPendingOperationRepository(current_event_session())

    def _transfers(self) -> PostgresPixTransferRepository:
        return PostgresPixTransferRepository(current_event_session())

    async def get_profile(self, *, customer_id: str) -> ProfileView:
        profile = await self._client.get_customer_profile(customer_id)
        return ProfileView(
            customer_id=profile.customer_id,
            name=profile.name,
            segment=profile.segment,
            account_ids=tuple(account.account_id for account in profile.accounts),
            card_ids=tuple(card.card_id for card in profile.cards),
            cards=tuple(
                CardReference(card_id=card.card_id, last4=card.last4) for card in profile.cards
            ),
        )

    async def get_limit(self, *, customer_id: str, card_id: str | None = None) -> LimitView:
        resolved_card_id = await self._card_id(customer_id, card_id)
        limit = await self._client.get_card_limit(customer_id, resolved_card_id)
        return LimitView(
            card_id=limit.card_id,
            last4=limit.last4,
            current_limit=limit.current_limit,
            used_amount=limit.used_amount,
        )

    async def get_balance(self, *, customer_id: str, account_id: str | None = None) -> BalanceView:
        account = await self._account_id(customer_id, account_id)
        return BalanceView(
            account_id=account,
            available_balance=await self._client.get_account_balance(customer_id, account),
        )

    async def get_invoice(self, *, customer_id: str, card_id: str | None = None) -> InvoiceView:
        invoice = await self._client.get_card_invoice(
            customer_id, await self._card_id(customer_id, card_id)
        )
        return InvoiceView(
            card_id=invoice.card_id,
            last4=invoice.last4,
            amount=invoice.amount,
            due_date=invoice.due_date,
            status=invoice.status,
        )

    async def get_statement(
        self, *, customer_id: str, account_id: str | None = None
    ) -> StatementView:
        account = await self._account_id(customer_id, account_id)
        statement = await self._client.get_account_statement(customer_id, account)
        return StatementView(
            account_id=account,
            entries=tuple((entry.description, entry.amount) for entry in statement.entries),
        )

    async def request_limit_change(
        self, *, user_id: uuid.UUID, customer_id: str, card_id: str | None, amount: Decimal
    ) -> LimitRequestView:
        result = await RequestCardLimitChangeUseCase(
            banking=self._client,
            operations=self._operations(),
            events=self._events,
            eligibility=self._eligibility,
            clock=self._clock,
            id_generator=self._ids,
            confirmation_ttl=timedelta(minutes=self._settings.confirmation_ttl_minutes),
        ).execute(
            RequestCardLimitChange(
                actor_user_id=user_id,
                customer_id=customer_id,
                card_id=await self._card_id(customer_id, card_id),
                new_limit=amount,
            )
        )
        if isinstance(result, LimitChangeRejected):
            return LimitRejectedView(reason=result.reason.value, maximum=result.maximum)
        if not isinstance(result, LimitChangeConfirmation):
            raise RuntimeError("Unexpected card-limit request result")
        return LimitConfirmationView(
            operation_hash=result.operation.operation_hash,
            current_limit=result.current_limit,
            requested_limit=amount,
            expires_at=result.operation.expires_at.isoformat(),
        )

    async def resolve_confirmation(
        self, *, user: object, user_id: uuid.UUID, operation_hash: str, response: str
    ) -> ConfirmationView:
        decision = await self._interpret_confirmation.interpret(response)
        operation = await self._operations().get_for_user(operation_hash, user_id)
        if operation is None:
            return None
        if operation.tool == "fazer_pix":
            executor = ExecutePixTransferUseCase(
                operations=self._operations(),
                transfers=self._transfers(),
                banking=self._client,
                events=self._events,
                clock=self._clock,
                id_generator=self._ids,
            )
            if self._clock.now() >= operation.expires_at:
                await executor.execute(operation_hash=operation_hash, user_id=user_id)
                return None
            if operation.status is not OperationStatus.PENDING_CONFIRMATION:
                return None
            if decision is ConfirmationDecision.AMBIGUOUS:
                return PixConfirmationView(
                    operation_hash=operation.operation_hash,
                    amount=Decimal(str(operation.params["amount"])),
                    recipient_key_masked=mask_pii(str(operation.params["recipient_key"])),
                    account_id=str(operation.params["account_id"]),
                    expires_at=operation.expires_at.isoformat(),
                )
            if decision is ConfirmationDecision.CANCEL:
                await executor.cancel(
                    operation_hash=operation_hash, user_id=user_id, reason="user_cancelled"
                )
                return None
            try:
                pix_receipt = await executor.execute(operation_hash=operation_hash, user_id=user_id)
            except BankingSystemError as error:
                # The executor already persisted FAILED + its events; swallowing
                # here lets the request transaction commit them (edge case:
                # SystemUnavailable at execute → failed + honest narration).
                return OperationFailedView(tool=operation.tool, reason=type(error).__name__)
            if pix_receipt is None:
                return None
            return PixReceiptView(
                transaction_id=pix_receipt.receipt.transaction_id,
                e2e_id=pix_receipt.receipt.e2e_id,
                amount=pix_receipt.receipt.amount,
                recipient_key_masked=pix_receipt.receipt.recipient_key_masked,
                account_id=pix_receipt.account_id,
            )
        if operation.tool != "alterar_limite":
            return None
        limit_executor = ExecuteCardLimitChangeUseCase(
            operations=self._operations(),
            banking=self._client,
            events=self._events,
            clock=self._clock,
            id_generator=self._ids,
        )
        if self._clock.now() >= operation.expires_at:
            # Reuse the execution transition to persist the expiry and publish
            # its event.  It never reaches MCP when the operation is expired.
            await limit_executor.execute(operation_hash=operation_hash, user_id=user_id)
            return None
        authorization = await self._authorization.authorize(
            user=user,
            action="update_card_limit",
            resource=ResourceRef(
                kind="card",
                owner_id=(
                    str(operation.params["customer_id"])
                    if operation.params.get("customer_id") is not None
                    else None
                ),
                id=(
                    str(operation.params["card_id"])
                    if operation.params.get("card_id") is not None
                    else None
                ),
            ),
        )
        if not authorization.permitted:
            await limit_executor.cancel(
                operation_hash=operation_hash,
                user_id=user_id,
                reason="authorization_revoked",
            )
            return LimitAuthorizationDeniedView(reason=authorization.reason or "role_forbidden")
        if decision is ConfirmationDecision.AMBIGUOUS:
            params = operation.params
            return LimitConfirmationView(
                operation_hash=operation.operation_hash,
                current_limit=Decimal(str(params.get("current_limit", "0"))),
                requested_limit=Decimal(str(params["new_limit"])),
                expires_at=operation.expires_at.isoformat(),
            )
        if decision is ConfirmationDecision.CANCEL:
            await limit_executor.cancel(
                operation_hash=operation_hash,
                user_id=user_id,
                reason="user_cancelled",
            )
            return None
        try:
            limit_receipt = await limit_executor.execute(
                operation_hash=operation_hash, user_id=user_id
            )
        except BankingSystemError as error:
            return OperationFailedView(tool=operation.tool, reason=type(error).__name__)
        if limit_receipt is None:
            return None
        return LimitReceiptView(
            old_limit=limit_receipt.old_limit,
            new_limit=limit_receipt.new_limit,
            last4=str(operation.params.get("last4", "****")),
        )

    async def request_pix(
        self, *, user_id: uuid.UUID, customer_id: str, recipient_key: str, amount: Decimal
    ) -> PixStepUpView | PixConfirmationView | PixRejectedView:
        result = await RequestPixTransferUseCase(
            banking=self._client,
            operations=self._operations(),
            transfers=self._transfers(),
            events=self._events,
            clock=self._clock,
            id_generator=self._ids,
            confirmation_ttl=timedelta(minutes=self._settings.confirmation_ttl_minutes),
            daily_limit=self._settings.pix_daily_limit,
            step_up_threshold=self._settings.pix_stepup_threshold,
        ).execute(
            RequestPixTransfer(
                actor_user_id=user_id,
                customer_id=customer_id,
                recipient_key=recipient_key,
                amount=amount,
            )
        )
        if isinstance(result, PixTransferRejected):
            return PixRejectedView(reason=result.reason, remaining_limit=result.remaining_limit)
        if result.requires_step_up:
            return PixStepUpView(
                operation_hash=result.operation.operation_hash,
                amount=result.amount,
                recipient_key_masked=result.recipient_key_masked,
                account_id=result.account_id,
                expires_at=result.operation.expires_at.isoformat(),
            )
        return PixConfirmationView(
            operation_hash=result.operation.operation_hash,
            amount=result.amount,
            recipient_key_masked=result.recipient_key_masked,
            account_id=result.account_id,
            expires_at=result.operation.expires_at.isoformat(),
        )

    async def resolve_step_up(
        self,
        *,
        user: object,
        user_id: uuid.UUID,
        operation_hash: str,
        challenge_id: uuid.UUID,
        code: str,
    ) -> PixConfirmationView | None:
        operation = await self._operations().get_for_user(operation_hash, user_id, lock=True)
        if (
            operation is None
            or operation.tool != "fazer_pix"
            or operation.status is not OperationStatus.PENDING_STEP_UP
        ):
            return None
        if self._clock.now() >= operation.expires_at:
            await ExecutePixTransferUseCase(
                operations=self._operations(),
                transfers=self._transfers(),
                banking=self._client,
                events=self._events,
                clock=self._clock,
                id_generator=self._ids,
            ).cancel(operation_hash=operation_hash, user_id=user_id, reason="step_up_expired")
            return None
        result = await VerifyStepUp(
            challenges=PostgresStepUpChallengeRepository(current_event_session()),
            clock=self._clock,
            id_generator=self._ids,
            event_publisher=self._events,
        ).execute(
            VerifyStepUpCommand(
                user=cast(AuthenticatedUser, user),
                challenge_id=challenge_id,
                operation_hash=operation_hash,
                code=code,
            )
        )
        if isinstance(result, Err):
            if result.error.code in {"step_up.locked", "step_up.expired"}:
                await ExecutePixTransferUseCase(
                    operations=self._operations(),
                    transfers=self._transfers(),
                    banking=self._client,
                    events=self._events,
                    clock=self._clock,
                    id_generator=self._ids,
                ).cancel(operation_hash=operation_hash, user_id=user_id, reason=result.error.code)
            return None
        operation = operation.complete_step_up(now=self._clock.now())
        await self._operations().save(operation)
        return PixConfirmationView(
            operation_hash=operation_hash,
            amount=Decimal(str(operation.params["amount"])),
            recipient_key_masked=mask_pii(str(operation.params["recipient_key"])),
            account_id=str(operation.params["account_id"]),
            expires_at=operation.expires_at.isoformat(),
        )

    async def _card_id(self, customer_id: str, card_id: str | None) -> str:
        if card_id is not None:
            return card_id
        profile = await self._client.get_customer_profile(customer_id)
        if len(profile.cards) != 1:
            raise ValueError("card selection is required")
        return profile.cards[0].card_id

    async def _account_id(self, customer_id: str, account_id: str | None) -> str:
        if account_id is not None:
            return account_id
        profile = await self._client.get_customer_profile(customer_id)
        if len(profile.accounts) != 1:
            raise ValueError("account selection is required")
        return profile.accounts[0].account_id
