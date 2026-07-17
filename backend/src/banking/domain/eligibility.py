"""BR-2 card-limit policy. Pure, deterministic and independently testable."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class LimitChangeDenial(StrEnum):
    NON_POSITIVE = "non_positive"
    NOT_MULTIPLE_OF_100 = "not_multiple_of_100"
    UNCHANGED = "unchanged"
    ABOVE_MAXIMUM = "above_maximum"
    BELOW_USED_AMOUNT = "below_used_amount"


@dataclass(frozen=True, kw_only=True)
class EligibilityDecision:
    eligible: bool
    maximum: Decimal
    reason: LimitChangeDenial | None = None


# BR-2.2 defaults: (score ≥800, ≥600, <600) per segment.
DEFAULT_MAXIMUMS: Mapping[str, tuple[Decimal, Decimal, Decimal]] = {
    "Personnalité": (Decimal("50000"), Decimal("25000"), Decimal("10000")),
    "Uniclass": (Decimal("30000"), Decimal("15000"), Decimal("8000")),
    "Varejo": (Decimal("15000"), Decimal("8000"), Decimal("4000")),
}


class EligibilityPolicy:
    """The BR-2.2 table; values are injected at the composition root
    (CARD_LIMIT_MAXIMUMS), score bands stay fixed per BR-2.2."""

    def __init__(
        self, maximums: Mapping[str, tuple[Decimal, Decimal, Decimal]] | None = None
    ) -> None:
        self._maximums = dict(maximums or DEFAULT_MAXIMUMS)

    def maximum_for(self, *, segment: str, credit_score: int) -> Decimal:
        fallback = self._maximums.get("Varejo", DEFAULT_MAXIMUMS["Varejo"])
        high, medium, low = self._maximums.get(segment, fallback)
        if credit_score >= 800:
            return high
        if credit_score >= 600:
            return medium
        return low

    def evaluate(
        self,
        *,
        segment: str,
        credit_score: int,
        current_limit: Decimal,
        used_amount: Decimal = Decimal("0"),
        requested_limit: Decimal,
    ) -> EligibilityDecision:
        maximum = self.maximum_for(segment=segment, credit_score=credit_score)
        if requested_limit <= 0:
            return EligibilityDecision(
                eligible=False, maximum=maximum, reason=LimitChangeDenial.NON_POSITIVE
            )
        if requested_limit % Decimal("100"):
            return EligibilityDecision(
                eligible=False, maximum=maximum, reason=LimitChangeDenial.NOT_MULTIPLE_OF_100
            )
        if requested_limit == current_limit:
            return EligibilityDecision(
                eligible=False, maximum=maximum, reason=LimitChangeDenial.UNCHANGED
            )
        if requested_limit < used_amount:
            return EligibilityDecision(
                eligible=False, maximum=maximum, reason=LimitChangeDenial.BELOW_USED_AMOUNT
            )
        if requested_limit > current_limit and requested_limit > maximum:
            return EligibilityDecision(
                eligible=False, maximum=maximum, reason=LimitChangeDenial.ABOVE_MAXIMUM
            )
        return EligibilityDecision(eligible=True, maximum=maximum)
