"""Persistence port for confirmation-bound banking operations."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from banking.domain.pending_operation import PendingOperation
from banking.domain.pix import PixTransfer
from banking.domain.values import PixReceipt


class PendingOperationRepository(Protocol):
    async def add(self, operation: PendingOperation) -> None: ...

    async def get(self, operation_hash: str, *, lock: bool = False) -> PendingOperation | None: ...

    async def save(self, operation: PendingOperation) -> None: ...

    async def expire_overdue(self, *, now: datetime) -> list[PendingOperation]: ...

    async def get_for_user(
        self, operation_hash: str, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None: ...

    async def get_active_for_user(
        self, user_id: uuid.UUID, *, lock: bool = False
    ) -> PendingOperation | None: ...


class PixTransferRepository(Protocol):
    async def reserve(self, transfer: PixTransfer, *, daily_limit: Decimal) -> Decimal | None: ...

    async def release(self, operation_hash: str) -> None: ...

    async def execute(self, operation_hash: str, receipt: PixReceipt) -> None: ...

    async def receipt_for(self, operation_hash: str) -> PixReceipt | None: ...
