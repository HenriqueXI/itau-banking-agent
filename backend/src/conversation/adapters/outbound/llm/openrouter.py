"""OpenRouter adapter — OpenAI-compatible HTTP, fallback chat provider (ADR-008).

Free-tier models here often ignore `response_format`, so the JSON schema is also
restated as an instruction. Tolerant parsing upstream is what actually holds.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    Usage,
)

logger = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class OpenRouterLlm:
    def __init__(self, *, api_key: str, model: str, base_url: str) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    @property
    def provider(self) -> str:
        return "openrouter"

    def _payload(
        self,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        wire = [{"role": m.role.value, "content": m.content} for m in messages]
        if json_schema is not None:
            wire.append(
                {
                    "role": "system",
                    "content": "Responda apenas com JSON válido para este schema: "
                    + json.dumps(json_schema, ensure_ascii=False),
                }
            )
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": wire,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(messages, json_schema, temperature, max_tokens, False),
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise LlmError(
                f"openrouter {exc.response.status_code}",
                provider=self.provider,
                retriable=exc.response.status_code == 429 or exc.response.status_code >= 500,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=True) from exc

        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(
                "openrouter returned no choices", provider=self.provider, retriable=True
            ) from exc

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            provider=self.provider,
            model=body.get("model", self._model),
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
        )

    async def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]:
        try:
            async with (
                httpx.AsyncClient(timeout=_TIMEOUT) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(messages, None, temperature, max_tokens, True),
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    delta = _parse_sse_delta(line)
                    if delta:
                        yield Delta(text=delta, provider=self.provider, model=self._model)
        except httpx.HTTPStatusError as exc:
            raise LlmError(
                f"openrouter {exc.response.status_code}",
                provider=self.provider,
                retriable=exc.response.status_code == 429 or exc.response.status_code >= 500,
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=True) from exc


def _parse_sse_delta(line: str) -> str | None:
    if not line.startswith("data: "):
        return None
    payload = line.removeprefix("data: ").strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        chunk = json.loads(payload)
        return chunk["choices"][0]["delta"].get("content")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None  # keepalives and non-content frames are normal, not errors
