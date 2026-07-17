"""Optional LLM-backed classifier for ambiguous confirmation replies (BR-6.3).

The classifier only labels the reply text — confirm/cancel/ambiguous. It never
authorizes: the pending-operation state machine (status, expiry, hash binding,
row lock) remains the sole gate on execution. Implementations live at the
composition root so `banking` stays independent of `conversation`.
"""

from typing import Protocol

from banking.application.confirmation import ConfirmationDecision


class ConfirmationClassifierPort(Protocol):
    async def classify(self, response: str) -> ConfirmationDecision:
        """Label `response`; may raise — callers must treat errors as AMBIGUOUS."""
        ...
