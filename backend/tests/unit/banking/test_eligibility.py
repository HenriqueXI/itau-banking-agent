from decimal import Decimal

import pytest

from banking.domain.eligibility import EligibilityPolicy, LimitChangeDenial


@pytest.mark.parametrize(
    ("segment", "score", "expected"),
    [
        ("Personnalité", 800, Decimal("50000")),
        ("Personnalité", 799, Decimal("25000")),
        ("Personnalité", 599, Decimal("10000")),
        ("Uniclass", 800, Decimal("30000")),
        ("Uniclass", 600, Decimal("15000")),
        ("Uniclass", 599, Decimal("8000")),
        ("Varejo", 800, Decimal("15000")),
        ("Varejo", 600, Decimal("8000")),
        ("Varejo", 599, Decimal("4000")),
    ],
)
def test_maximum_matches_br2_2(segment: str, score: int, expected: Decimal) -> None:
    assert EligibilityPolicy().maximum_for(segment=segment, credit_score=score) == expected


@pytest.mark.parametrize(
    ("new_limit", "reason"),
    [
        (Decimal("0"), LimitChangeDenial.NON_POSITIVE),
        (Decimal("5050"), LimitChangeDenial.NOT_MULTIPLE_OF_100),
        (Decimal("5000"), LimitChangeDenial.UNCHANGED),
        (Decimal("50100"), LimitChangeDenial.ABOVE_MAXIMUM),
    ],
)
def test_invalid_or_ineligible_increases_are_denied(
    new_limit: Decimal, reason: LimitChangeDenial
) -> None:
    decision = EligibilityPolicy().evaluate(
        segment="Personnalité",
        credit_score=820,
        current_limit=Decimal("5000"),
        requested_limit=new_limit,
    )

    assert not decision.eligible
    assert decision.reason is reason
    assert decision.maximum == Decimal("50000")


def test_decrease_is_eligible_even_when_above_profile_maximum() -> None:
    decision = EligibilityPolicy().evaluate(
        segment="Varejo",
        credit_score=500,
        current_limit=Decimal("6000"),
        requested_limit=Decimal("5000"),
    )

    assert decision.eligible
    assert decision.maximum == Decimal("4000")


def test_injected_table_overrides_br2_2_defaults() -> None:
    policy = EligibilityPolicy(
        maximums={"Personnalité": (Decimal("99000"), Decimal("50000"), Decimal("20000"))}
    )

    assert policy.maximum_for(segment="Personnalité", credit_score=820) == Decimal("99000")
    # Unknown segments fall back to the injected Varejo row, or BR-2.2 when absent.
    assert policy.maximum_for(segment="Uniclass", credit_score=820) == Decimal("15000")


def test_unknown_segment_falls_back_to_varejo() -> None:
    assert EligibilityPolicy().maximum_for(segment="Private", credit_score=820) == Decimal("15000")
