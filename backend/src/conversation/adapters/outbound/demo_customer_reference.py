"""Static customer-reference resolver for the bounded challenge demo."""

import unicodedata

from conversation.application.ports.customer_reference import CustomerReferenceResolverPort
from shared.demo_personas import DEMO_PERSONAS


class DemoCustomerReferenceResolver(CustomerReferenceResolverPort):
    """Resolve only the seeded demo personas; this is not a customer search.

    The mapping is derived from the same immutable personas used by the seed
    script and MCP demo core.  It performs no database or MCP access, so a
    reference can be normalized before the deterministic authorization check.
    """

    def __init__(self) -> None:
        self._references = {
            alias: persona.customer_id
            for persona in DEMO_PERSONAS
            if persona.customer_id is not None
            for alias in (
                _normalize(persona.customer_id),
                _normalize(persona.name),
                _normalize(f"{persona.name} Souza"),
            )
        }

    async def resolve(self, reference: str) -> str | None:
        return self._references.get(_normalize(reference))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_accents = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(without_accents.casefold().split())
