"""Audit query use case remains testable with a fake port."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from audit.application.dto import AuditPage, AuditQuery
from audit.application.use_cases.query_audit import QueryAudit
from audit.domain.entities import AuditEvent


class FakeAuditLogRepository:
    def __init__(self, page: AuditPage) -> None:
        self.page = page
        self.query: AuditQuery | None = None

    async def append(self, event: AuditEvent) -> bool:
        return True

    async def list(self, query: AuditQuery) -> AuditPage:
        self.query = query
        return self.page

    async def get_by_id(self, audit_id: uuid.UUID) -> AuditEvent | None:
        return next((item for item in self.page.items if item.id == audit_id), None)


def audit_event() -> AuditEvent:
    return AuditEvent(
        id=uuid.UUID(int=1),
        event_id=uuid.UUID(int=2),
        user_ref="user-1",
        action="PIX",
        amount=Decimal("20.00"),
        occurred_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        resource="customer:123",
        outcome="executed",
        trace_id="trace-1",
        details={"safe": "value"},
    )


async def test_returns_page_from_port() -> None:
    page = AuditPage(items=(audit_event(),), total=1)
    repository = FakeAuditLogRepository(page)
    use_case = QueryAudit(repository=repository)
    query = AuditQuery(user_ref="user-1", action="PIX", page=2, page_size=10)

    assert await use_case.list(query) == page
    assert repository.query == query


async def test_finds_detail_by_id() -> None:
    item = audit_event()
    use_case = QueryAudit(repository=FakeAuditLogRepository(AuditPage(items=(item,), total=1)))

    assert await use_case.get(item.id) == item


@pytest.mark.parametrize(
    "query",
    [
        AuditQuery(page=0),
        AuditQuery(page_size=0),
        AuditQuery(page_size=101),
        AuditQuery(
            from_at=datetime(2026, 7, 16, tzinfo=UTC),
            to_at=datetime(2026, 7, 15, tzinfo=UTC),
        ),
    ],
)
def test_rejects_invalid_pagination_or_date_range(query: AuditQuery) -> None:
    with pytest.raises(ValueError):
        QueryAudit.validate(query)
