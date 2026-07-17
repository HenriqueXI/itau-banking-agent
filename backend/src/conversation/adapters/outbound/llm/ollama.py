"""Ollama adapter — local/offline provider (ADR-008, compose profile `local-llm`).

Last in the fallback chain and the only one that needs no key, which is what
makes an offline demo (and a quota-free CI eval) possible.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from conversation.application.ports.llm import (
    Completion,
    Delta,
    LlmError,
    LlmMessage,
    Usage,
)

_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


class OllamaLlm:
    def __init__(self, *, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def provider(self) -> str:
        return "ollama"

    def _payload(
        self,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_schema is not None:
            payload["format"] = "json"
        return payload

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
                    f"{self._base_url}/api/chat",
                    json=self._payload(messages, json_schema, temperature, max_tokens, False),
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise LlmError(
                f"ollama {exc.response.status_code}",
                provider=self.provider,
                retriable=exc.response.status_code >= 500,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=True) from exc

        return Completion(
            text=(body.get("message") or {}).get("content", ""),
            provider=self.provider,
            model=body.get("model", self._model),
            usage=Usage(
                input_tokens=body.get("prompt_eval_count", 0) or 0,
                output_tokens=body.get("eval_count", 0) or 0,
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
                    f"{self._base_url}/api/chat",
                    json=self._payload(messages, None, temperature, max_tokens, True),
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = (chunk.get("message") or {}).get("content")
                    if text:
                        yield Delta(text=text, provider=self.provider, model=self._model)
        except httpx.HTTPStatusError as exc:
            raise LlmError(
                f"ollama {exc.response.status_code}",
                provider=self.provider,
                retriable=exc.response.status_code >= 500,
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmError(str(exc), provider=self.provider, retriable=True) from exc
