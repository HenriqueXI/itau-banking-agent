"""Tolerant JSON parsing for structured LLM output (llm-providers.md §4).

Not every provider enforces a schema natively, and free-tier models fence their
JSON in markdown, prepend prose, or trail a comma. This module extracts the
object without ever *guessing* its content: it repairs syntax, never semantics.
A parse that fails returns None — the caller then clarifies, and never invents
an operation (langgraph.md edge case: malformed JSON → repair → clarify).
"""

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in `text`, or None if there isn't a usable one."""
    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _candidates(text: str) -> list[str]:
    stripped = text.strip()
    candidates = [stripped]

    fenced = _FENCE.search(stripped)
    if fenced:
        candidates.append(fenced.group(1))

    braced = _outermost_object(stripped)
    if braced:
        candidates.append(braced)

    return [_TRAILING_COMMA.sub(r"\1", c) for c in candidates if c]


def _outermost_object(text: str) -> str | None:
    """Slice from the first `{` to its matching `}` — survives prose on both
    sides. String-aware so a brace inside a value doesn't shift the depth."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
