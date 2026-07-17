"""Gemini adapter (`google-genai`) — primary chat provider (ADR-008).

Structured output goes through `response_mime_type=application/json` plus the
schema; parsing stays tolerant upstream anyway (llm-providers.md §4) because a
free-tier model that ignores the schema must degrade to a clarify, not a crash.
"""

from collections.abc import AsyncIterator
from typing import Any

import structlog
from google import genai
from google.genai import types

from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    MessageRole,
    Usage,
)

logger = structlog.get_logger(__name__)

_RETRIABLE_MARKERS = ("429", "500", "502", "503", "504", "resource_exhausted", "unavailable")


def _is_retriable(error: Exception) -> bool:
    """429/5xx rotate providers; 4xx is our bug and must surface (ADR-008)."""
    text = str(error).lower()
    return any(marker in text for marker in _RETRIABLE_MARKERS)


def _split(messages: list[LlmMessage]) -> tuple[str | None, list[types.Content]]:
    """Gemini takes the system role out-of-band; the rest map 1:1."""
    system = "\n\n".join(m.content for m in messages if m.role is MessageRole.SYSTEM) or None
    contents = [
        types.Content(
            role="model" if m.role is MessageRole.ASSISTANT else "user",
            parts=[types.Part.from_text(text=m.content)],
        )
        for m in messages
        if m.role is not MessageRole.SYSTEM
    ]
    if not contents:  # a system-only prompt still needs a turn to answer
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="Prossiga.")])]
    return system, contents


class GeminiLlm:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @property
    def provider(self) -> str:
        return "gemini"

    def _config(
        self,
        system: str | None,
        json_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_schema else None,
        )

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion:
        system, contents = _split(messages)
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config(system, json_schema, temperature, max_tokens),
            )
        except Exception as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=_is_retriable(exc)) from exc

        usage = getattr(response, "usage_metadata", None)
        return Completion(
            text=response.text or "",
            provider=self.provider,
            model=self._model,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            ),
        )

    async def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]:
        system, contents = _split(messages)
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=self._config(system, None, temperature, max_tokens),
            )
            async for chunk in stream:
                if chunk.text:
                    yield Delta(text=chunk.text, provider=self.provider, model=self._model)
        except Exception as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=_is_retriable(exc)) from exc
