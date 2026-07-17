"""Input guardrails I1-I5 (guardrails.md §1) — pure, deterministic, fail-closed.

Checks run in order and each appends a flag; the caller (the `input_guardrails`
node) applies the worst disposition. Patterns-only for I2 in v1 (PRD-006 open
question #1: model assist is added only if the adversarial suite shows gaps).

I3 (scope) is not here: scope routing is `understand`'s job (guardrails.md §1).
"""

import re
import unicodedata
from dataclasses import dataclass

from conversation.domain.values import Disposition, GuardrailFlag

MAX_INPUT_CHARS = 4000

# I1 — control characters other than tab/newline carry no meaning in chat input
# and are a classic delimiter-smuggling vehicle.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2028\u2029\u202a-\u202e\ufeff]")

# I2 — instruction-override / role-play / system-prompt fishing / delimiter smuggling.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"(ignor[ea]|esque[çc]a|desconsidere|disregard|forget)\s+"
            r"(todas?\s+)?(as\s+|the\s+|suas?\s+|your\s+)?"
            r"(instru[çc][õo]es|regras|orienta[çc][õo]es|instructions|rules|prompts?)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_play",
        re.compile(
            r"(aja|atue|finja|comporte-se|pretend|act)\s+(como|as|that|you)\b"
            r"|voc[êe]\s+(agora\s+)?[ée]\s+(um|uma|o|a)\s+\w+"
            r"|modo\s+(desenvolvedor|developer|dan|admin)"
            r"|jailbreak|sem\s+restri[çc][õo]es|without\s+restrictions",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_fishing",
        re.compile(
            r"(mostre|revele|repita|imprima|qual\s+[ée]|me\s+d[êe]|show|reveal|repeat|print)"
            r"[^.?!]{0,40}"
            r"(system\s*prompt|prompt\s+do\s+sistema|suas?\s+instru[çc][õo]es|"
            r"suas?\s+regras|seu\s+prompt|initial\s+instructions|tool\s+schema|"
            r"esquema\s+de\s+ferramentas)",
            re.IGNORECASE,
        ),
    ),
    (
        "delimiter_smuggling",
        re.compile(
            r"(<\s*/?\s*(system|assistant|tool|instructions)\s*>)"
            r"|(\[\s*(system|assistant)\s*\])"
            r"|(^|\n)\s*(system|assistant)\s*:",
            re.IGNORECASE,
        ),
    ),
    (
        "authorization_bypass",
        re.compile(
            r"(pule|pular|ignore|dispense|sem)\s+(a\s+)?(confirma[çc][ãa]o|autentica[çc][ãa]o|"
            r"valida[çc][ãa]o|verifica[çc][ãa]o\s+em\s+duas\s+etapas)"
            r"|(voc[êe]|agente)\s+(tem|possui)\s+permiss[ãa]o\s+(total|de\s+admin)"
            r"|me\s+(d[êe]|conceda)\s+(acesso|permiss[ãa]o)\s+(de\s+)?(admin|total)",
            re.IGNORECASE,
        ),
    ),
)

# I4 — third-party PII solicitation cue: a CPF or a full name that isn't "meu/minha".
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_THIRD_PARTY_TARGET = re.compile(
    r"\b(saldo|limite|extrato|perfil|dados|conta|cart[ãa]o)\s+(d[oa]s?\s+|de\s+)"
    r"(?!meu|minha|mim\b)"
    r"([A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÀ-ÿ]+(\s+[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÀ-ÿ]+)+)",
)

# I5 — credentials pasted by the user. Sanitized from state, never checkpointed.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("password", re.compile(r"(senha|password|pwd)\s*(é|e|:|=)\s*\S+", re.IGNORECASE)),
    ("card_number", re.compile(r"\b(?:\d[ .-]?){13,19}\b")),
    ("cvv", re.compile(r"\b(cvv|cvc|c[óo]digo\s+de\s+seguran[çc]a)\s*(:|=)?\s*\d{3,4}\b", re.I)),
    ("token", re.compile(r"\b(sk-|pk-|Bearer\s+)[A-Za-z0-9._\-]{12,}", re.IGNORECASE)),
    ("api_key", re.compile(r"\b(api[_\s-]?key|token|chave)\s*(:|=)\s*\S{8,}", re.IGNORECASE)),
)

_REDACTED = "[REDACTED]"


@dataclass(frozen=True, kw_only=True)
class InputInspection:
    """Outcome of the full input ring: the text to keep + why."""

    text: str
    flags: tuple[GuardrailFlag, ...]

    @property
    def blocked(self) -> bool:
        return any(f.blocking for f in self.flags)

    @property
    def sanitized(self) -> bool:
        return any(f.disposition is Disposition.SANITIZE for f in self.flags)

    @property
    def third_party_cue(self) -> bool:
        return any(f.check_id == "I4" for f in self.flags)


def inspect_input(text: str, *, max_chars: int = MAX_INPUT_CHARS) -> InputInspection:
    """Run I1 → I5 in order. Blocks short-circuit: no point sanitizing text we
    refuse to process, and the flag list stays the reason trail for the event."""
    flags: list[GuardrailFlag] = []

    normalized, size_flag = _check_size_and_encoding(text, max_chars)
    if size_flag is not None:
        flags.append(size_flag)
        if size_flag.blocking:
            return InputInspection(text=normalized, flags=tuple(flags))

    injection = _check_injection(normalized)
    if injection is not None:
        flags.append(injection)
        return InputInspection(text=normalized, flags=tuple(flags))

    third_party = _check_third_party_cue(normalized)
    if third_party is not None:
        flags.append(third_party)

    sanitized, secret_flag = _check_secrets(normalized)
    if secret_flag is not None:
        flags.append(secret_flag)

    return InputInspection(text=sanitized, flags=tuple(flags))


def _check_size_and_encoding(text: str, max_chars: int) -> tuple[str, GuardrailFlag | None]:
    normalized = unicodedata.normalize("NFKC", text)
    stripped = _ZERO_WIDTH.sub("", _CONTROL_CHARS.sub("", normalized)).strip()

    if not stripped:
        return stripped, GuardrailFlag(
            check_id="I1", disposition=Disposition.BLOCK, detail="empty or whitespace-only input"
        )
    if len(stripped) > max_chars:
        return stripped, GuardrailFlag(
            check_id="I1",
            disposition=Disposition.BLOCK,
            detail=f"input exceeds {max_chars} chars ({len(stripped)})",
        )
    if stripped != text:
        return stripped, GuardrailFlag(
            check_id="I1",
            disposition=Disposition.SANITIZE,
            detail="stripped control/zero-width characters, normalized unicode",
        )
    return stripped, None


def _check_injection(text: str) -> GuardrailFlag | None:
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailFlag(
                check_id="I2", disposition=Disposition.BLOCK, detail=f"injection pattern: {name}"
            )
    return None


def _check_third_party_cue(text: str) -> GuardrailFlag | None:
    """A cue is not a denial — it hardens the downstream ownership check. The
    decision still belongs to `authorize` (ADR-011)."""
    if _CPF.search(text) or _THIRD_PARTY_TARGET.search(text):
        return GuardrailFlag(
            check_id="I4",
            disposition=Disposition.FLAG,
            detail="possible third-party data solicitation — strict ownership check",
        )
    return None


def _check_secrets(text: str) -> tuple[str, GuardrailFlag | None]:
    sanitized = text
    hits: list[str] = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(sanitized):
            hits.append(name)
            sanitized = pattern.sub(_REDACTED, sanitized)
    if not hits:
        return text, None
    return sanitized, GuardrailFlag(
        check_id="I5",
        disposition=Disposition.SANITIZE,
        detail=f"credentials redacted before state: {', '.join(sorted(hits))}",
    )
