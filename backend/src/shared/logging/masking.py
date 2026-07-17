"""Shared PII masking — the single implementation reused by logger, tracer
adapter and event constructors (security.md §6, data-flow.md §6).

Patterns covered: CPF (formatted + bare 11 digits), email, BR phone numbers,
PIX random keys (UUID). Masking is deliberately greedy: an 11-digit number
could be a CPF or a phone — either way it must not leave the process unmasked.
"""

import re
from collections.abc import Mapping
from typing import Any

# Correlation/internal identifiers are UUID-shaped but not PII; masking them
# would break the traceability they exist for (FR-7.2). Shared by the log
# processor and the tracer adapter — one list, or the two drift.
CORRELATION_KEYS = frozenset(
    {
        "trace_id",
        "request_id",
        "event_id",
        "correlation_id",
        "thread_id",
        "span_id",
        "run_id",
        "operation_hash",
    }
)

# Order matters: email before phone (digits inside a local part must not be
# phone-masked first), formatted CPF before bare digit runs.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_CPF_FORMATTED = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_PHONE = re.compile(
    r"(?<![\d.\-])(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?\d{4,5}[\s.-]?\d{4}(?![\d.\-])"
)


def _mask_email(match: re.Match[str]) -> str:
    local = match.group(0).split("@", 1)[0]
    return f"{local[:3]}****"


def _mask_cpf(match: re.Match[str]) -> str:
    digits = match.group(0)
    return f"***.{digits[4:7]}.{digits[8:11]}-**"


def _mask_uuid(match: re.Match[str]) -> str:
    return f"{match.group(0)[:4]}****"


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def mask_pii(text: str) -> str:
    """Mask every configured PII pattern in `text`."""
    text = _EMAIL.sub(_mask_email, text)
    text = _CPF_FORMATTED.sub(_mask_cpf, text)
    text = _UUID.sub(_mask_uuid, text)
    text = _PHONE.sub(_mask_phone, text)
    return text


def mask_value(value: Any) -> Any:
    """Recursively mask strings inside dicts/lists/tuples; other types pass through."""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {key: mask_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_value(item) for item in value)
    return value


def mask_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Mask a key→value mapping, leaving `CORRELATION_KEYS` intact."""
    return {
        key: value if key in CORRELATION_KEYS else mask_value(value)
        for key, value in values.items()
    }
