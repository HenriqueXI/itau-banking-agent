"""TracedLlm: every LLM call becomes a Langfuse generation, with token usage.

A decorator around `LlmPort` rather than tracing inside each node, for the
reason ADR-010 gives: instrument at boundaries. The port *is* the boundary, so
one wrapper covers `understand`, its repair pass, `generate_answer`, the O2
judge — and every LLM call PRD-007+ adds, without those PRDs writing a line of
telemetry.

The generation is named after the node span it lands in (`current_scope()`),
which is how telemetry.md §1's `Generation understand` gets its name without the
port knowing what a graph node is.

Wrapping order matters: this sits *outside* `FallbackLlm`, so `provider` and
`model` on the generation are whichever provider actually answered — a turn that
failed over to OpenRouter must say so, not report the configured primary.
"""

from collections.abc import AsyncIterator
from typing import Any

from conversation.application.ports.llm import Completion, Delta, LlmMessage, LlmPort
from shared.application.ports.tracer import GenerationSpec, current_scope


class TracedLlm:
    def __init__(self, inner: LlmPort) -> None:
        self._inner = inner

    @property
    def provider(self) -> str:
        return self._inner.provider

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> Completion:
        scope = current_scope()
        if scope is None:
            return await self._inner.complete(
                messages, json_schema=json_schema, temperature=temperature, max_tokens=max_tokens
            )

        spec = GenerationSpec(
            name=scope.name,
            # Pre-call these are the chain's *intent*. `record_completion` below
            # replaces them with whoever actually answered — a turn that failed
            # over to the fallback must say so.
            provider=self._inner.provider,
            model="",
            temperature=temperature,
            max_tokens=max_tokens,
            input=_serialize(messages),
            metadata={"structured_output": json_schema is not None},
        )
        with scope.generation(spec) as generation:
            completion = await self._inner.complete(
                messages, json_schema=json_schema, temperature=temperature, max_tokens=max_tokens
            )
            generation.record_completion(
                provider=completion.provider,
                model=completion.model,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
            )
            generation.update(output=completion.text)
            return completion

    async def stream(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[Delta]:
        """Streams carry no usage in the delta contract, so the generation
        records what it can: the text and the provider/model that served it, at
        end-of-stream, with tokens left unknown rather than reported as zero.
        A client disconnect closes the generator, and the `finally` is why the
        record still lands (PRD-013 edge case) — a trace that only exists for
        completed streams hides exactly the turns worth looking at.
        """
        scope = current_scope()
        if scope is None:
            async for delta in self._inner.stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                yield delta
            return

        spec = GenerationSpec(
            name=scope.name,
            provider=self._inner.provider,
            model="",
            temperature=temperature,
            max_tokens=max_tokens,
            input=_serialize(messages),
            metadata={"streamed": True},
        )
        with scope.generation(spec) as generation:
            chunks: list[str] = []
            served_by: tuple[str, str] | None = None
            try:
                async for delta in self._inner.stream(
                    messages, temperature=temperature, max_tokens=max_tokens
                ):
                    chunks.append(delta.text)
                    served_by = (delta.provider, delta.model)
                    yield delta
            finally:
                if served_by is not None:
                    generation.record_completion(provider=served_by[0], model=served_by[1])
                generation.update(output="".join(chunks))


def _serialize(messages: list[LlmMessage]) -> list[dict[str, str]]:
    return [{"role": message.role.value, "content": message.content} for message in messages]
