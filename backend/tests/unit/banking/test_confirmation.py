import pytest

from banking.application.confirmation import (
    ConfirmationDecision,
    InterpretConfirmation,
    match_confirmation,
)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("confirm", ConfirmationDecision.CONFIRM),
        ("confirmo", ConfirmationDecision.CONFIRM),
        ("cancel", ConfirmationDecision.CANCEL),
        ("cancelar", ConfirmationDecision.CANCEL),
        ("confirmo e aumenta mais mil", ConfirmationDecision.AMBIGUOUS),
        ("talvez", ConfirmationDecision.AMBIGUOUS),
    ],
)
def test_confirmation_is_an_exact_deterministic_match(
    response: str, expected: ConfirmationDecision
) -> None:
    assert match_confirmation(response) is expected


class RecordingClassifier:
    def __init__(
        self,
        decision: ConfirmationDecision = ConfirmationDecision.AMBIGUOUS,
        *,
        error: Exception | None = None,
    ) -> None:
        self._decision = decision
        self._error = error
        self.calls: list[str] = []

    async def classify(self, response: str) -> ConfirmationDecision:
        self.calls.append(response)
        if self._error is not None:
            raise self._error
        return self._decision


@pytest.mark.parametrize("response", ["confirmo", "cancelar", "sim", "não"])
async def test_deterministic_match_never_reaches_the_classifier(response: str) -> None:
    classifier = RecordingClassifier(ConfirmationDecision.CANCEL)

    decision = await InterpretConfirmation(classifier).interpret(response)

    assert decision is match_confirmation(response)
    assert classifier.calls == []


@pytest.mark.parametrize("labeled", [ConfirmationDecision.CONFIRM, ConfirmationDecision.CANCEL])
async def test_ambiguous_reply_is_labeled_by_the_classifier(
    labeled: ConfirmationDecision,
) -> None:
    classifier = RecordingClassifier(labeled)

    decision = await InterpretConfirmation(classifier).interpret("pode seguir com isso aí")

    assert decision is labeled
    assert classifier.calls == ["pode seguir com isso aí"]


async def test_classifier_ambiguous_stays_ambiguous_and_reasks() -> None:
    classifier = RecordingClassifier(ConfirmationDecision.AMBIGUOUS)

    decision = await InterpretConfirmation(classifier).interpret("confirmo mas aumenta mais mil")

    assert decision is ConfirmationDecision.AMBIGUOUS


async def test_classifier_error_fails_closed_to_ambiguous() -> None:
    classifier = RecordingClassifier(error=RuntimeError("provider down"))

    decision = await InterpretConfirmation(classifier).interpret("manda ver")

    assert decision is ConfirmationDecision.AMBIGUOUS


async def test_without_classifier_ambiguous_stays_ambiguous() -> None:
    decision = await InterpretConfirmation(None).interpret("manda ver")

    assert decision is ConfirmationDecision.AMBIGUOUS
