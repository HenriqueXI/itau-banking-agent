"""Fail-closed confirmation parsing; no model decides a money-affecting action.

The deterministic allowlist decides first. Only replies it cannot place may be
labeled by an optional classifier (PRD007-FR-5) — and the classifier can only
ever narrow AMBIGUOUS to confirm/cancel; any error or unknown label stays
AMBIGUOUS, which re-asks. Execution gates (status, expiry, hash, lock) are
enforced downstream regardless of the label.
"""

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from banking.application.ports.confirmation_classifier import ConfirmationClassifierPort


class ConfirmationDecision(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    AMBIGUOUS = "ambiguous"


_CONFIRM = frozenset({"confirmar", "confirmo", "sim", "pode confirmar"})
_CANCEL = frozenset({"cancelar", "cancela", "não", "nao", "desistir"})


def match_confirmation(response: str) -> ConfirmationDecision:
    normalized = " ".join(response.casefold().strip().split())
    if normalized == "confirm":
        return ConfirmationDecision.CONFIRM
    if normalized == "cancel":
        return ConfirmationDecision.CANCEL
    if normalized in _CONFIRM:
        return ConfirmationDecision.CONFIRM
    if normalized in _CANCEL:
        return ConfirmationDecision.CANCEL
    return ConfirmationDecision.AMBIGUOUS


class InterpretConfirmation:
    """Deterministic matcher first; classifier only on AMBIGUOUS, fail-closed."""

    def __init__(self, classifier: "ConfirmationClassifierPort | None" = None) -> None:
        self._classifier = classifier

    async def interpret(self, response: str) -> ConfirmationDecision:
        decision = match_confirmation(response)
        if decision is not ConfirmationDecision.AMBIGUOUS or self._classifier is None:
            return decision
        try:
            return await self._classifier.classify(response)
        except Exception:
            return ConfirmationDecision.AMBIGUOUS
