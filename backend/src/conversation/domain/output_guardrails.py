"""Output guardrails O1, O3, O4, O6 (guardrails.md §2) — the deterministic ring.

O2 (grounding) is judge-assisted and lives in the node (it needs a port); O5
(amount integrity) lands with operations in PRD-007. Everything here is pure:
given text + what produced it, decide mask / block / regenerate.

Design rule (guardrails.md §5): determinism first — the judge never gates what a
pattern can decide.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from conversation.domain.values import Citation, Disposition, GuardrailFlag

# O4 — canary: strings that only ever appear inside our own prompt scaffolding.
# If one reaches the output, the model is reciting its instructions.
PROMPT_CANARY = "ITAU-AGENT-SYSTEM-CANARY"

_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("canary", re.compile(re.escape(PROMPT_CANARY), re.IGNORECASE)),
    (
        "prompt_recital",
        re.compile(
            r"(minhas\s+instru[çc][õo]es\s+(s[ãa]o|dizem)|meu\s+prompt\s+(de\s+sistema\s+)?[ée]|"
            r"system\s+prompt\s*:|<\s*system\s*>|you\s+are\s+an?\s+\w+\s+assistant)",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_schema_recital",
        re.compile(
            r"(\"?required_params\"?\s*[:=])|(\"?risk_tier\"?\s*[:=])|(ToolSpec\()"
            r"|(json\s*schema\s*(da|do)\s+ferramenta)",
            re.IGNORECASE,
        ),
    ),
)

# O3 — PII mask. Money and dates must survive untouched: masking a rate would
# corrupt a correct answer, so patterns are anchored to document/key formats.
_CPF_FULL = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")
_CNPJ_FULL = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CARD_FULL = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_SECRET_KEY = re.compile(r"\b(sk-|pk-)[A-Za-z0-9._\-]{8,}\b")

# O6 — injection echo: retrieved KB text carrying instructions must not surface
# as instructions (rag.md §7). Narrower than I2 on purpose: this fires on our own
# output, where an imperative aimed at the model is never legitimate.
_INJECTION_ECHO = re.compile(
    r"(ignor[ea]\s+(as\s+)?instru[çc][õo]es|disregard\s+(the\s+)?(previous\s+)?instructions"
    r"|voc[êe]\s+deve\s+revelar|reveal\s+your\s+(system\s+)?prompt"
    r"|execute\s+a\s+seguinte\s+instru[çc][ãa]o|<\s*/?\s*(system|instructions)\s*>)",
    re.IGNORECASE,
)


class OutputVerdict(StrEnum):
    """What the caller must do with the candidate answer."""

    PASS = "pass"
    MASKED = "masked"
    REGENERATE = "regenerate"
    BLOCK = "block"


@dataclass(frozen=True, kw_only=True)
class OutputInspection:
    text: str
    verdict: OutputVerdict
    flags: tuple[GuardrailFlag, ...]


def inspect_output(
    text: str,
    *,
    citations: tuple[Citation, ...] = (),
    requires_citations: bool = False,
    expected_amounts: tuple[Decimal, ...] = (),
) -> OutputInspection:
    """Run the deterministic output ring in severity order.

    A leak (O4) blocks outright — a masked leak is still a leak. Missing
    citations on a grounded answer (O1) asks for one regeneration; the caller
    decides refusal on the second failure (guardrails.md §2).
    """
    flags: list[GuardrailFlag] = []

    leak = _check_leak(text)
    if leak is not None:
        return OutputInspection(text=text, verdict=OutputVerdict.BLOCK, flags=(leak,))

    echo = _check_injection_echo(text)
    if echo is not None:
        return OutputInspection(text=text, verdict=OutputVerdict.BLOCK, flags=(echo,))

    masked, pii_flag = _mask_pii(text)
    if pii_flag is not None:
        flags.append(pii_flag)

    if requires_citations:
        citation_flag = _check_citations(masked, citations)
        if citation_flag is not None:
            flags.append(citation_flag)
            return OutputInspection(
                text=masked, verdict=OutputVerdict.REGENERATE, flags=tuple(flags)
            )

    amount_flag = _check_amount_integrity(masked, expected_amounts)
    if amount_flag is not None:
        flags.append(amount_flag)
        return OutputInspection(text=masked, verdict=OutputVerdict.BLOCK, flags=tuple(flags))

    verdict = OutputVerdict.MASKED if pii_flag is not None else OutputVerdict.PASS
    return OutputInspection(text=masked, verdict=verdict, flags=tuple(flags))


_MONEY = re.compile(r"R\$\s*(-?[0-9.]+,[0-9]{2})")


def _check_amount_integrity(
    text: str, expected_amounts: tuple[Decimal, ...]
) -> GuardrailFlag | None:
    """O5: a typed banking narration may not substitute a monetary amount."""
    if not expected_amounts:
        return None
    try:
        actual = tuple(
            Decimal(item.replace(".", "").replace(",", ".")) for item in _MONEY.findall(text)
        )
    except InvalidOperation:
        actual = ()
    if actual == expected_amounts:
        return None
    return GuardrailFlag(
        check_id="O5",
        disposition=Disposition.BLOCK,
        detail="narrated monetary values differ from typed result",
    )


def _check_citations(text: str, citations: tuple[Citation, ...]) -> GuardrailFlag | None:
    """O1: a KB answer without a citation payload AND a marker is unusable —
    the user can't verify it and BR-8.2 requires both."""
    if not citations:
        return GuardrailFlag(
            check_id="O1",
            disposition=Disposition.BLOCK,
            detail="grounded answer carries no citation payload",
        )
    if not any(c.marker() in text for c in citations):
        return GuardrailFlag(
            check_id="O1",
            disposition=Disposition.BLOCK,
            detail="answer text carries no citation marker",
        )
    return None


def _mask_pii(text: str) -> tuple[str, GuardrailFlag | None]:
    """O3: keep the last digits so a receipt stays readable, drop the rest."""
    hits: list[str] = []
    masked = text

    def _sub(
        name: str, pattern: re.Pattern[str], repl: str | Callable[[re.Match[str]], str]
    ) -> None:
        nonlocal masked
        if pattern.search(masked):
            hits.append(name)
            masked = pattern.sub(repl, masked)

    _sub("cpf", _CPF_FULL, lambda m: f"***.***.{m.group(3)}-**")
    _sub("cnpj", _CNPJ_FULL, "**.***.***/****-**")
    _sub("card", _CARD_FULL, lambda m: f"****{re.sub(r'[^0-9]', '', m.group(0))[-4:]}")
    _sub("email", _EMAIL, lambda m: f"{m.group(0)[0]}***@{m.group(0).split('@')[1]}")
    _sub("secret_key", _SECRET_KEY, "[REDACTED]")

    if not hits:
        return text, None
    return masked, GuardrailFlag(
        check_id="O3",
        disposition=Disposition.SANITIZE,
        detail=f"masked in place: {', '.join(sorted(set(hits)))}",
    )


def _check_leak(text: str) -> GuardrailFlag | None:
    for name, pattern in _LEAK_PATTERNS:
        if pattern.search(text):
            return GuardrailFlag(
                check_id="O4", disposition=Disposition.BLOCK, detail=f"prompt/tool leak: {name}"
            )
    return None


def _check_injection_echo(text: str) -> GuardrailFlag | None:
    if _INJECTION_ECHO.search(text):
        return GuardrailFlag(
            check_id="O6",
            disposition=Disposition.BLOCK,
            detail="retrieved content surfaced as instructions",
        )
    return None
