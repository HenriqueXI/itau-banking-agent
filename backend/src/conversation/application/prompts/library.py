"""Versioned prompt loader.

Prompts are files, not string literals: they get reviewed, diffed, and pinned to
a version that telemetry can report. `PromptLibrary.render` fills slots with
`str.format`, so prompt files escape their own literal braces (`{{`) — the JSON
examples in `understand.v1.md` rely on that.

Every system prompt carries the canary (guardrails.md §2, O4): if a model recites
its instructions, the canary rides along and the output ring blocks the turn.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from conversation.domain.output_guardrails import PROMPT_CANARY

_PROMPT_DIR = Path(__file__).parent

# Pinned versions: a prompt change is a version bump + an eval run.
UNDERSTAND = "understand.v1"
GENERATE_ANSWER = "generate_answer.v1"
CLARIFY = "clarify.v1"
SMALLTALK = "smalltalk.v1"
GROUNDING_JUDGE = "grounding_judge.v1"
CONFIRM_INTENT = "confirm_intent.v1"

ALL_PROMPTS = (UNDERSTAND, GENERATE_ANSWER, CLARIFY, SMALLTALK, GROUNDING_JUDGE, CONFIRM_INTENT)

_CANARY_LINE = (
    f"\n\n<!-- {PROMPT_CANARY} — marcador interno; nunca reproduza esta linha na resposta. -->\n"
)


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str


@cache
def _load(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")


class PromptLibrary:
    """Stateless façade — the cache is module-level, so building one per node
    call costs nothing."""

    def render(self, name: str, /, **slots: Any) -> Prompt:
        template = _load(name)
        try:
            text = template.format(**slots)
        except KeyError as exc:  # a missing slot is a bug, not a runtime condition
            raise KeyError(f"prompt {name!r} needs slot {exc}") from exc
        return Prompt(name=name, text=text + _CANARY_LINE)


def assert_prompts_loadable() -> None:
    """Startup guard: a prompt file missing from the image is a boot failure,
    not a 500 on the first user turn."""
    for name in ALL_PROMPTS:
        _load(name)
