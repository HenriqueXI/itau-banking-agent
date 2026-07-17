"""Langfuse adapter for `TracerPort` (ADR-010).

Three properties this file exists to guarantee, in priority order:

1. **Nothing leaks.** Every payload crossing into the SDK goes through
   `mask_value` first — the same processors the logger uses (ADR-010: "masking
   must happen in our adapter"). There is exactly one door (`_export`), so the
   security test has exactly one thing to prove.
2. **Nothing breaks.** Every SDK call is wrapped: a failure degrades the scope
   to no-op and the turn continues (FR-7 is observability, not a dependency).
   Warnings are rate-limited — a Langfuse outage lasting an hour must not turn
   into an hour of log spam that hides the real incident. That includes the
   SDK's *own* logger (`_route_sdk_logs`), which fails on a background thread
   where our try/except cannot reach it.
3. **Nothing blocks.** The SDK ships events on background threads through a
   bounded queue (100k, non-blocking put, drops when full), so the request path
   never waits on the network — which is why `flush()` is for shutdown and
   scripts only.
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from shared.application.ports.tracer import (
    Generation,
    GenerationSpec,
    Level,
    Scope,
    TraceSpec,
    use_scope,
)
from shared.logging.masking import mask_mapping, mask_value

logger = structlog.get_logger(__name__)

#: One "langfuse is down" warning per this many seconds. Long enough that an
#: outage costs a handful of lines, short enough to still see it recovering.
WARN_INTERVAL_SECONDS = 60.0

#: The SDK's HTTP timeout. Kept small: an unreachable host must fail fast on a
#: background thread, not hold one open for the default 20s.
_HTTP_TIMEOUT_SECONDS = 5


class _WarnOnce:
    """Rate-limited warning (PRD013-FR-6: "a single warning per interval, no
    spam"). Monotonic, so a clock step can't unmute it."""

    def __init__(
        self,
        *,
        interval_seconds: float = WARN_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval_seconds
        self._now = now
        self._last: float | None = None
        self.suppressed = 0

    def __call__(self, error: BaseException | str) -> None:
        moment = self._now()
        if self._last is not None and moment - self._last < self._interval:
            self.suppressed += 1
            return
        logger.warning(
            "telemetry.export_failed",
            error=str(error),
            error_type=type(error).__name__ if isinstance(error, BaseException) else "SdkLog",
            suppressed_since_last=self.suppressed,
        )
        self._last = moment
        self.suppressed = 0


class _WarnOnceHandler(logging.Handler):
    """Routes the Langfuse SDK's own log records through our rate limiter."""

    def __init__(self, warn: _WarnOnce) -> None:
        super().__init__(level=logging.WARNING)
        self._warn = warn

    def emit(self, record: logging.LogRecord) -> None:
        self._warn(record.getMessage())


def _route_sdk_logs(warn: _WarnOnce) -> None:
    """Take over the `langfuse` logger.

    The SDK ships events from a background thread, so an unreachable Langfuse
    fails *there* — outside every try/except in this file — and the SDK logs one
    ERROR per failed batch. That is spam (the acceptance criterion says one
    warning per interval) and it is the wrong level: degraded observability is a
    warning, `error` means a broken request (telemetry.md §2).

    So the records are re-routed rather than silenced: silencing would make an
    outage invisible, since our own calls only enqueue and always succeed.
    """
    sdk_logger = logging.getLogger("langfuse")
    sdk_logger.handlers = [_WarnOnceHandler(warn)]
    sdk_logger.propagate = False
    sdk_logger.setLevel(logging.WARNING)


def _export(value: Any) -> Any:
    """The masking chokepoint. Nothing reaches the SDK except through here."""
    return mask_value(value)


def _metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Same rule as the log pipeline: mask everything except correlation ids,
    which are the whole point of the trace (`mask_mapping`)."""
    if not metadata:
        return None
    return mask_mapping(metadata)


class LangfuseScope:
    """Wraps one Langfuse stateful client. `_client is None` means the SDK call
    that would have created it failed — the scope then behaves as a no-op, so a
    broken tracer degrades instead of cascading."""

    def __init__(self, name: str, client: Any | None, warn: _WarnOnce) -> None:
        self._name = name
        self._client = client
        self._warn = warn

    @property
    def name(self) -> str:
        return self._name

    def _child(self, factory: str, **kwargs: Any) -> Any | None:
        if self._client is None:
            return None
        try:
            return getattr(self._client, factory)(**kwargs)
        except Exception as error:  # SDK/network failure — never the caller's problem
            self._warn(error)
            return None

    @contextmanager
    def span(
        self,
        name: str,
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[Scope]:
        child = self._child("span", name=name, input=_export(input), metadata=_metadata(metadata))
        scope = LangfuseScope(name, child, self._warn)
        with use_scope(scope):
            yield from _closing(scope)

    @contextmanager
    def generation(self, spec: GenerationSpec) -> Iterator[Generation]:
        child = self._child(
            "generation",
            name=spec.name,
            model=spec.model,
            input=_export(spec.input),
            model_parameters=_model_parameters(spec),
            metadata=_metadata({**spec.metadata, "provider": spec.provider}),
            version=spec.prompt_version,
        )
        generation = LangfuseGeneration(spec.name, child, self._warn)
        with use_scope(generation):
            yield from _closing(generation)

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: Level | None = None,
        status_message: str | None = None,
    ) -> None:
        if self._client is None:
            return
        fields = _present(
            output=_export(output),
            metadata=_metadata(metadata),
            level=level,
            status_message=status_message,
        )
        if not fields:
            return
        try:
            self._client.update(**fields)
        except Exception as error:
            self._warn(error)

    def end(self, **fields: Any) -> None:
        """Close the span. Not on `Scope`: the context manager owns the
        lifetime, so no caller should ever need to call this."""
        if self._client is None:
            return
        try:
            self._client.end(**fields)
        except Exception as error:
            self._warn(error)


class LangfuseGeneration(LangfuseScope):
    def record_completion(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if self._client is None:
            return
        fields: dict[str, Any] = {"metadata": {"provider": provider}}
        if model:
            fields["model"] = model
        if input_tokens is not None or output_tokens is not None:
            fields["usage"] = {
                "input": input_tokens or 0,
                "output": output_tokens or 0,
                "unit": "TOKENS",
            }
        try:
            self._client.update(**fields)
        except Exception as error:
            self._warn(error)


class LangfuseTracer:
    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str,
        environment: str = "local",
        release: str | None = None,
        warn: _WarnOnce | None = None,
        client: Any | None = None,
    ) -> None:
        self._warn = warn or _WarnOnce()
        _route_sdk_logs(self._warn)
        self._client = (
            client
            if client is not None
            else self._connect(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
                release=release,
                environment=environment,
            )
        )

    def _connect(self, **kwargs: Any) -> Any | None:
        from langfuse import Langfuse

        try:
            return Langfuse(
                **kwargs,
                timeout=_HTTP_TIMEOUT_SECONDS,
                # Boot must not depend on Langfuse being reachable: the SDK
                # verifies auth lazily, and a wrong key costs traces, not turns.
                enabled=True,
            )
        except Exception as error:
            self._warn(error)
            return None

    @contextmanager
    def trace(self, spec: TraceSpec) -> Iterator[Scope]:
        client: Any | None = None
        if self._client is not None:
            try:
                client = self._client.trace(
                    id=spec.trace_id,
                    name=spec.name,
                    user_id=spec.user_id,
                    session_id=spec.session_id,
                    tags=list(spec.tags) or None,
                    input=_export(spec.input),
                    metadata=_metadata(spec.metadata),
                )
            except Exception as error:
                self._warn(error)

        scope = LangfuseScope(spec.name, client, self._warn)
        with use_scope(scope):
            try:
                yield scope
            except Exception as error:
                scope.update(level="ERROR", status_message=type(error).__name__)
                raise
            # A trace has no `end()` in the SDK; its spans carry the timing.

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as error:
            self._warn(error)


def _closing(scope: LangfuseScope) -> Iterator[Any]:
    """Yield the scope, then end it — marking ERROR if the block raised.

    The span must close on the exception path too: an unended span is an orphan
    in the UI, and the node wrapper's whole job is that exceptions are visible.
    """
    try:
        yield scope
    except Exception as error:
        scope.end(level="ERROR", status_message=f"{type(error).__name__}: {error}")
        raise
    scope.end()


def _model_parameters(spec: GenerationSpec) -> dict[str, Any] | None:
    return _present(temperature=spec.temperature, max_tokens=spec.max_tokens) or None


def _present(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}
