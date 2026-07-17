"""PostgreSQL repository with explicit row locking for execution transitions."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from banking.adapters.outbound.postgres.tables import (
    pending_operations,
    pix_daily_buckets,
    pix_transfers,
)
from banking.domain.pending_operation import OperationStatus, PendingOperation
from banking.domain.pix import PixTransfer
from banking.domain.values import PixReceipt


def _operation(row: Any) -> PendingOperation:
    return PendingOperation(
        operation_id=row.operation_id,
        user_id=row.user_id,
        tool=row.tool,
        params=dict(row.params),
        tier=row.tier,
        operation_hash=row.operation_hash,
        status=OperationStatus(row.status),
        created_at=row.created_at,
        expires_at=row.expires_at,
        cancellation_reason=row.cancellation_reason,
        idempotency_key=row.idempotency_key,
        resolved_at=row.resolved_at,
    )


class PostgresPendingOperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, operation: PendingOperation) -> None:
        await self._session.execute(
            pending_operations.insert().values(
                operation_id=operation.operation_id,
                user_id=operation.user_id,
                tool=operation.tool,
                params=operation.params,
                tier=operation.tier,
                operation_hash=operation.operation_hash,
                status=operation.status.value,
                created_at=operation.created_at,
                expires_at=operation.expires_at,
                cancellation_reason=operation.cancellation_reason,
                idempotency_key=operation.idempotency_key,
                resolved_at=operation.resolved_at,
            )
        )

    async def get(self, operation_hash: str, *, lock: bool = False) -> PendingOperation | None:
        statement = select(pending_operations).where(
            pending_operations.c.operation_hash == operation_hash
        )
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return _operation(row) if row is not None else None

    async def get_for_user(
        self, operation_hash: str, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None:
        statement = select(pending_operations).where(
            and_(
                pending_operations.c.operation_hash == operation_hash,
                pending_operations.c.user_id == user_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return _operation(row) if row is not None else None

    async def get_active_for_user(
        self, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None:
        statement = select(pending_operations).where(
            and_(
                pending_operations.c.user_id == user_id,
                pending_operations.c.status.in_(
                    (
                        OperationStatus.PENDING_STEP_UP.value,
                        OperationStatus.PENDING_CONFIRMATION.value,
                    )
                ),
            )
        )
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return _operation(row) if row is not None else None

    async def save(self, operation: PendingOperation) -> None:
        await self._session.execute(
            update(pending_operations)
            .where(pending_operations.c.operation_id == operation.operation_id)
            .values(
                status=operation.status.value,
                cancellation_reason=operation.cancellation_reason,
                resolved_at=operation.resolved_at,
            )
        )

    async def expire_overdue(self, *, now: datetime) -> list[PendingOperation]:
        result = await self._session.execute(
            select(pending_operations)
            .where(
                and_(
                    pending_operations.c.status.in_(
                        (
                            OperationStatus.PENDING_STEP_UP.value,
                            OperationStatus.PENDING_CONFIRMATION.value,
                        )
                    ),
                    pending_operations.c.expires_at <= now,
                )
            )
            .with_for_update(skip_locked=True)
        )
        operations = [_operation(row).expire(now=now) for row in result.all()]
        for operation in operations:
            await self.save(operation)
        return operations


class PostgresPixTransferRepository:
    """Reservation ledger serialised by a customer/day row (BR-3.2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, transfer: PixTransfer, *, daily_limit: Decimal) -> Decimal | None:
        await self._session.execute(
            insert(pix_daily_buckets)
            .values(
                customer_id=transfer.customer_id,
                local_day=transfer.local_day,
                pending_amount=Decimal("0"),
                executed_amount=Decimal("0"),
            )
            .on_conflict_do_nothing(index_elements=["customer_id", "local_day"])
        )
        result = await self._session.execute(
            select(pix_daily_buckets)
            .where(
                and_(
                    pix_daily_buckets.c.customer_id == transfer.customer_id,
                    pix_daily_buckets.c.local_day == transfer.local_day,
                )
            )
            .with_for_update()
        )
        bucket = result.one()
        used = Decimal(str(bucket.pending_amount)) + Decimal(str(bucket.executed_amount))
        remaining = daily_limit - used
        if transfer.amount > remaining:
            return remaining
        await self._session.execute(
            pix_transfers.insert().values(
                operation_hash=transfer.operation_hash,
                customer_id=transfer.customer_id,
                account_id=transfer.account_id,
                recipient_key=transfer.recipient_key,
                recipient_key_masked=transfer.recipient_key_masked,
                amount=transfer.amount,
                local_day=transfer.local_day,
                status="reserved",
            )
        )
        await self._session.execute(
            update(pix_daily_buckets)
            .where(
                and_(
                    pix_daily_buckets.c.customer_id == transfer.customer_id,
                    pix_daily_buckets.c.local_day == transfer.local_day,
                )
            )
            .values(pending_amount=Decimal(str(bucket.pending_amount)) + transfer.amount)
        )
        return None

    async def release(self, operation_hash: str) -> None:
        transfer = await self._get_for_update(operation_hash)
        if transfer is None or transfer.status != "reserved":
            return
        await self._change_bucket(
            transfer, pending_delta=-transfer.amount, executed_delta=Decimal("0")
        )
        await self._session.execute(
            update(pix_transfers)
            .where(pix_transfers.c.operation_hash == operation_hash)
            .values(status="released")
        )

    async def execute(self, operation_hash: str, receipt: PixReceipt) -> None:
        transfer = await self._get_for_update(operation_hash)
        if transfer is None or transfer.status == "executed":
            return
        if transfer.status == "reserved":
            await self._change_bucket(
                transfer, pending_delta=-transfer.amount, executed_delta=transfer.amount
            )
        await self._session.execute(
            update(pix_transfers)
            .where(pix_transfers.c.operation_hash == operation_hash)
            .values(
                status="executed",
                receipt={
                    "transaction_id": receipt.transaction_id,
                    "status": receipt.status,
                    "amount": str(receipt.amount),
                    "recipient_key_masked": receipt.recipient_key_masked,
                    "executed_at": receipt.executed_at.isoformat(),
                    "e2e_id": receipt.e2e_id,
                },
            )
        )

    async def receipt_for(self, operation_hash: str) -> PixReceipt | None:
        result = await self._session.execute(
            select(pix_transfers.c.receipt).where(pix_transfers.c.operation_hash == operation_hash)
        )
        payload = result.scalar_one_or_none()
        if not isinstance(payload, dict):
            return None
        from datetime import datetime

        return PixReceipt(
            transaction_id=str(payload["transaction_id"]),
            status=str(payload["status"]),
            amount=Decimal(str(payload["amount"])),
            recipient_key_masked=str(payload["recipient_key_masked"]),
            executed_at=datetime.fromisoformat(str(payload["executed_at"])),
            e2e_id=str(payload["e2e_id"]),
        )

    async def _get_for_update(self, operation_hash: str) -> Any | None:
        result = await self._session.execute(
            select(pix_transfers)
            .where(pix_transfers.c.operation_hash == operation_hash)
            .with_for_update()
        )
        return result.one_or_none()

    async def _change_bucket(
        self, transfer: Any, *, pending_delta: Decimal, executed_delta: Decimal
    ) -> None:
        result = await self._session.execute(
            select(pix_daily_buckets)
            .where(
                and_(
                    pix_daily_buckets.c.customer_id == transfer.customer_id,
                    pix_daily_buckets.c.local_day == transfer.local_day,
                )
            )
            .with_for_update()
        )
        bucket = result.one()
        await self._session.execute(
            update(pix_daily_buckets)
            .where(
                and_(
                    pix_daily_buckets.c.customer_id == transfer.customer_id,
                    pix_daily_buckets.c.local_day == transfer.local_day,
                )
            )
            .values(
                pending_amount=Decimal(str(bucket.pending_amount)) + pending_delta,
                executed_amount=Decimal(str(bucket.executed_amount)) + executed_delta,
            )
        )
