"""Resolve a demo customer reference into its canonical banking identifier."""

from typing import Protocol


class CustomerReferenceResolverPort(Protocol):
    """Maps a user-supplied demo persona reference without reading banking data.

    The graph uses this before RBAC.  A successful value is a canonical
    ``customer_id`` suitable for authorization and, only after permission, MCP.
    """

    async def resolve(self, reference: str) -> str | None: ...
