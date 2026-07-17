"""`understand`: the one node where the LLM decides anything (langgraph.md §2).

It classifies intent, extracts params, resolves references — temperature 0, JSON
schema out. Everything it produces is a *claim* the graph then checks: the tool
name is looked up in the registry, the params are validated against that tool's
schema, the target is handed to `authorize`. A model that invents `intent:
"admin_override"` produces an unknown intent, not a privilege.

Malformed output never crashes and never guesses: repair pass → clarify.
"""

import re
import unicodedata
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.json_repair import parse_json_object
from conversation.application.ports.llm import LlmError, LlmMessage, MessageRole
from conversation.application.prompts import library
from conversation.domain.history import render_history, window
from conversation.domain.tools import REGISTRY, tool_for, tool_for_intent
from conversation.domain.values import Intent, ResourceRef, Understanding

logger = structlog.get_logger(__name__)

__all__ = [
    "drop_unsupported_params",
    "make_understand",
    "normalize_amount",
    "parse_json_object",
    "parse_understanding",
]

UNDERSTAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "tool": {"type": "string", "nullable": True},
        "params": {"type": "object"},
        "target_resource": {
            "type": "object",
            "nullable": True,
            "properties": {
                "kind": {"type": "string"},
                "owner_id": {"type": "string", "nullable": True},
                "id": {"type": "string", "nullable": True},
            },
        },
        "references_resolved": {"type": "boolean"},
        "missing_param": {"type": "string", "nullable": True},
        "ambiguity": {"type": "string", "nullable": True},
    },
    "required": ["intent"],
}

_AMOUNT_PARAMS = frozenset({"amount", "value", "valor"})

# Words that may sit inside a worded amount without being a number themselves.
_AMOUNT_FILLERS = frozenset({"reais", "real", "pila", "conto", "contos", "de", "e"})

_WORD_AMOUNTS = {
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "três": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "cem": 100,
    "cento": 100,
    "duzentos": 200,
    "quinhentos": 500,
    "mil": 1000,
}


def _tools_catalog() -> str:
    lines = []
    for spec in REGISTRY.values():
        required = ", ".join(sorted(spec.required_params)) or "—"
        optional = ", ".join(sorted(spec.optional_params)) or "—"
        lines.append(
            f"- `{spec.name}` (intent `{spec.intent.value}`): "
            f"obrigatórios: {required}; opcionais: {optional}"
        )
    return "\n".join(lines)


def make_understand(deps: GraphDependencies) -> GraphNode:
    async def understand(state: AgentState) -> AgentState:
        pending_selection = state.get("pending_card_selection")
        last4 = _pending_card_last4(state.get("input_text", ""))
        if isinstance(pending_selection, Understanding) and last4 is not None:
            # A four-digit reply to our own deterministic card prompt is data,
            # not a fresh natural-language intent. Preserve the original,
            # already-authorized intent and let resolve_resource validate the
            # final four digits against the current MCP profile.
            return {
                "understanding": replace(
                    pending_selection,
                    params={**pending_selection.params, "card_id": last4},
                    ambiguity=None,
                    references_resolved=True,
                )
            }
        history = window(state.get("messages", [])[:-1], deps.config.history_window_turns)
        prompt = deps.prompts.render(
            library.UNDERSTAND,
            tools=_tools_catalog(),
            history=render_history(history) or "(sem histórico)",
            message=state.get("input_text", ""),
        )
        messages = [LlmMessage(role=MessageRole.SYSTEM, content=prompt.text)]

        completion = await deps.llm.complete(
            messages,
            json_schema=UNDERSTAND_SCHEMA,
            temperature=deps.config.extraction_temperature,
            max_tokens=deps.config.understand_max_tokens,
        )
        understanding = parse_understanding(completion.text)

        if understanding is None:
            understanding = await _repair(deps, messages, completion.text)

        if understanding is None:
            # Repair failed: ask, never guess an operation (langgraph.md §6 edge case).
            logger.warning("graph.understand.unparseable", thread_id=state["thread_id"])
            understanding = Understanding(
                intent=Intent.UNCLEAR,
                ambiguity="não consegui identificar o pedido",
            )

        understanding = drop_unsupported_params(
            understanding,
            message=state.get("input_text", ""),
            history=render_history(history),
        )

        logger.info(
            "graph.understand",
            thread_id=state["thread_id"],
            intent=understanding.intent.value,
            tool=understanding.tool,
            references_resolved=understanding.references_resolved,
            provider=completion.provider,
        )
        return {
            "understanding": understanding,
            "provider": completion.provider,
            "pending_card_selection": None,
            "clarification_response": None,
        }

    return understand


def _pending_card_last4(message: str) -> str | None:
    match = re.fullmatch(r"\s*(?:final\s+)?(\d{4})\s*", message, flags=re.IGNORECASE)
    return match.group(1) if match else None


async def _repair(
    deps: GraphDependencies, messages: list[LlmMessage], bad_output: str
) -> Understanding | None:
    """One repair pass: hand the model its own broken output and ask for the
    object alone. Cheaper than a clarify turn, and bounded at exactly one try."""
    repair_messages = [
        *messages,
        LlmMessage(role=MessageRole.ASSISTANT, content=bad_output),
        LlmMessage(
            role=MessageRole.USER,
            content="Sua resposta anterior não era um JSON válido do schema. "
            "Responda novamente APENAS com o objeto JSON, sem markdown e sem texto extra.",
        ),
    ]
    try:
        completion = await deps.llm.complete(
            repair_messages,
            json_schema=UNDERSTAND_SCHEMA,
            temperature=0.0,
            max_tokens=deps.config.understand_max_tokens,
        )
    except LlmError:
        return None
    return parse_understanding(completion.text)


def drop_unsupported_params(
    understanding: Understanding, *, message: str, history: str
) -> Understanding:
    """Delete any parameter the conversation never mentioned.

    A model that copies `irmao@email.com` out of a few-shot example, or fills an
    amount nobody said, has invented an operation's parameters. Prompts ask it
    not to; this makes it impossible. An invented value is dropped, which turns
    the turn into a clarify — the agent asks instead of guessing (FR-1.4),
    exactly as it would if the model had reported the gap itself.

    `query` is exempt: a KB search string is a *rewrite* of the question, not a
    value quoted from it.
    """
    if not understanding.params:
        return understanding

    haystack = f"{message}\n{history}"
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in understanding.params.items():
        if name == "query" or _is_supported(name, value, haystack):
            kept[name] = value
        else:
            dropped.append(name)

    if not dropped:
        return understanding

    logger.warning("graph.understand.invented_params", params=dropped)
    spec = tool_for(understanding.tool)
    missing = spec.missing_params(kept) if spec else ()
    return replace(
        understanding,
        params=kept,
        missing_param=missing[0] if missing else understanding.missing_param,
    )


def _is_supported(name: str, value: Any, haystack: str) -> bool:
    if name in _AMOUNT_PARAMS:
        return _amount_mentioned(value, haystack)
    return _text_mentioned(str(value), haystack)


def _amount_mentioned(value: Any, haystack: str) -> bool:
    """The amount must equal one the conversation actually states — in digits
    ("10.000"), words ("dez mil"), or a mix ("10 mil")."""
    try:
        target = Decimal(str(value))
    except InvalidOperation:
        return False
    return any(candidate == target for candidate in _amounts_in(haystack))


def _amounts_in(text: str) -> list[Decimal]:
    candidates: list[Decimal] = []
    for fragment in re.findall(r"[\d][\d.,]*\s*(?:mil|milh[õo]es|milh[ãa]o)?", text.lower()):
        parsed = _parse_numeric(fragment.strip())
        if parsed is not None:
            candidates.append(parsed)
    for fragment in re.findall(
        r"(?:[a-zà-ÿ]+\s+)?(?:mil|milh[õo]es|milh[ãa]o)|\b(?:cem|quinhentos|duzentos)\b",
        text.lower(),
    ):
        parsed = _parse_worded(fragment.strip())
        if parsed is not None:
            candidates.append(parsed)
    return candidates


def _text_mentioned(value: str, haystack: str) -> bool:
    """Punctuation-insensitive containment: a CPF key typed as "123.456.789-00"
    supports the extraction "12345678900"."""
    folded_value, folded_haystack = _fold(value), _fold(haystack)
    if folded_value and folded_value in folded_haystack:
        return True
    stripped_value = re.sub(r"[^a-z0-9@]", "", folded_value)
    stripped_haystack = re.sub(r"[^a-z0-9@]", "", folded_haystack)
    return bool(stripped_value) and stripped_value in stripped_haystack


def _fold(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def parse_understanding(text: str) -> Understanding | None:
    """Map raw model text to the domain value, dropping anything unrecognized.

    Unknown intents/tools/params are discarded rather than passed through: the
    registry is the allowlist, and an unregistered capability has no code path.
    """
    payload = parse_json_object(text)
    if payload is None:
        return None

    try:
        intent = Intent(str(payload.get("intent", "")).strip().lower())
    except ValueError:
        return None

    spec = tool_for(_as_str(payload.get("tool"))) or tool_for_intent(intent)
    # A tool naming a different intent is model noise; the intent wins because
    # routing keys off it.
    if spec is not None and spec.intent is not intent:
        spec = tool_for_intent(intent)

    params = _clean_params(payload.get("params"), allowed=spec.params if spec else frozenset())
    missing = _as_str(payload.get("missing_param"))
    if spec is not None:
        # Trust the tool schema over the model's self-report of what's missing.
        computed = spec.missing_params(params)
        missing = computed[0] if computed else None

    return Understanding(
        intent=intent,
        tool=spec.name if spec else None,
        params=params,
        target_resource=_parse_resource(payload.get("target_resource"), spec),
        references_resolved=bool(payload.get("references_resolved", False)),
        missing_param=missing,
        ambiguity=_as_str(payload.get("ambiguity")),
    )


def _clean_params(raw: Any, *, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key)
        if allowed and name not in allowed:
            continue
        if value in (None, ""):
            continue
        cleaned[name] = normalize_amount(value) if name in _AMOUNT_PARAMS else value
    return cleaned


def _parse_resource(raw: Any, spec: Any) -> ResourceRef | None:
    if not isinstance(raw, dict):
        return None
    owner = _as_str(raw.get("owner_id"))
    kind = _as_str(raw.get("kind")) or (spec.resource_kind if spec else None)
    if kind is None:
        return None
    return ResourceRef(kind=kind, owner_id=owner, id=_as_str(raw.get("id")))


def normalize_amount(value: Any) -> Any:
    """Money is `Decimal`, never float — including when it arrives as prose.

    Handles "10 mil", "R$ 10.000,00", "dez mil reais", 10000, "10000.50".
    Anything unrecognized passes through untouched so validation can reject it
    honestly instead of a silent zero.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return value

    text = value.strip().lower().replace("r$", "").strip()
    numeric = _parse_numeric(text)
    if numeric is not None:
        return numeric
    return _parse_worded(text) or value


def _parse_numeric(text: str) -> Decimal | None:
    match = re.search(r"\d[\d.,]*", text)
    if match is None:
        return None

    digits = match.group(0)
    if "," in digits:  # pt-BR: dot groups thousands, comma is the decimal separator
        digits = digits.replace(".", "").replace(",", ".")
    elif digits.count(".") == 1 and len(digits.split(".")[1]) == 3:
        digits = digits.replace(".", "")  # "10.000" is ten thousand, not ten
    else:
        digits = digits.replace(".", "") if digits.count(".") > 1 else digits

    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None

    if re.search(r"\bmil\b", text):
        amount *= 1000
    elif re.search(r"\b(milh[õo]es|milh[ãa]o)\b", text):
        amount *= 1_000_000
    return amount


def _parse_worded(text: str) -> Decimal | None:
    """Only parse a phrase that is *entirely* an amount.

    "um valor qualquer" contains "um"; reading it as R$ 1,00 would invent a
    parameter out of a filler word. Every word must be a number word or a
    currency filler, or this isn't an amount at all.
    """
    words = re.findall(r"[a-zà-ÿ]+", text)
    if not words or any(w not in _WORD_AMOUNTS and w not in _AMOUNT_FILLERS for w in words):
        return None
    total = Decimal(0)
    current = Decimal(0)
    seen = False
    for word in words:
        unit = _WORD_AMOUNTS.get(word)
        if unit is None:
            continue
        seen = True
        if unit >= 1000:
            current = current or Decimal(1)
            total += current * unit
            current = Decimal(0)
        else:
            current += unit
    total += current
    return total if seen and total > 0 else None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
