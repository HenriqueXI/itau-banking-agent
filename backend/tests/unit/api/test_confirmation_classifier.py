"""LlmConfirmationClassifier: label extraction is tolerant, authorization is
not its job — everything unparseable degrades to AMBIGUOUS (re-ask)."""

import pytest

from api.banking_wiring import LlmConfirmationClassifier
from banking.application.confirmation import ConfirmationDecision, InterpretConfirmation
from conversation.application.ports.llm import LlmError
from tests.fakes.conversation import ScriptedLlm


@pytest.mark.parametrize(
    ("llm_text", "expected"),
    [
        ('{"decision": "confirm"}', ConfirmationDecision.CONFIRM),
        ('{"decision": "cancel"}', ConfirmationDecision.CANCEL),
        ('{"decision": "ambiguous"}', ConfirmationDecision.AMBIGUOUS),
        ('Claro! ```json\n{"decision": "confirm"}\n```', ConfirmationDecision.CONFIRM),
        ('{"decision": "CONFIRM"}', ConfirmationDecision.CONFIRM),
        ('{"decision": "execute_now"}', ConfirmationDecision.AMBIGUOUS),
        ("não sei dizer", ConfirmationDecision.AMBIGUOUS),
        ("{}", ConfirmationDecision.AMBIGUOUS),
    ],
)
async def test_classifier_parses_labels_and_degrades_garbage_to_ambiguous(
    llm_text: str, expected: ConfirmationDecision
) -> None:
    classifier = LlmConfirmationClassifier(ScriptedLlm(default=llm_text))

    assert await classifier.classify("pode seguir") is expected


async def test_prompt_carries_the_reply_and_only_the_reply() -> None:
    llm = ScriptedLlm(default='{"decision": "confirm"}')

    await LlmConfirmationClassifier(llm).classify("manda bala então")

    prompt = "\n".join(m.content for m in llm.calls[0])
    assert "manda bala então" in prompt
    assert "confirm" in prompt  # the label vocabulary is in the instructions


async def test_llm_error_propagates_and_interpret_fails_closed() -> None:
    failing = ScriptedLlm(fail_with=LlmError("quota", provider="fake"))
    classifier = LlmConfirmationClassifier(failing)

    with pytest.raises(LlmError):
        await classifier.classify("pode seguir")

    decision = await InterpretConfirmation(classifier).interpret("pode seguir")
    assert decision is ConfirmationDecision.AMBIGUOUS
