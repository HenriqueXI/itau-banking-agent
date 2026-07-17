"""The Langfuse adapter against an SDK stub (PRD-013 acceptance table).

Two of the three acceptance criteria live here, because both are properties of
this file and nothing else: masking happens in the adapter (ADR-010), and a
Langfuse outage cannot reach a turn (PRD013-FR-6).
"""

import logging
from typing import Any

import pytest
from pytest import MonkeyPatch

import shared.adapters.langfuse_tracer as tracer_module
from shared.adapters.langfuse_tracer import LangfuseTracer, _WarnOnce
from shared.application.ports.tracer import GenerationSpec, TraceSpec
from tests.fakes import StubSdk

# The PRD's marker: a syntactically valid CPF, so a masking regression shows up
# as this exact string in an export rather than as a plausible-looking one.
CPF_MARKER = "111.444.777-35"
EMAIL_MARKER = "roberto.silva@empresa.com.br"


def _tracer(sdk: Any, warn: _WarnOnce | None = None) -> LangfuseTracer:
    return LangfuseTracer(
        public_key="pk", secret_key="sk", host="http://langfuse:3000", client=sdk, warn=warn
    )


def _spec(**overrides: Any) -> TraceSpec:
    base: dict[str, Any] = {
        "name": "turn",
        "trace_id": "tr-1",
        "session_id": "t-1",
        "user_id": "u-1",
    }
    return TraceSpec(**{**base, **overrides})


class TestSpanEmission:
    def test_a_turn_emits_a_trace_with_session_grouping(self) -> None:
        sdk = StubSdk()
        with _tracer(sdk).trace(_spec(input="oi")):
            pass
        (trace,) = sdk.of_kind("trace")
        assert trace.payload["id"] == "tr-1"
        assert trace.payload["name"] == "turn"
        # Session = conversation: without this Langfuse shows loose turns.
        assert trace.payload["session_id"] == "t-1"
        assert trace.payload["user_id"] == "u-1"

    def test_spans_nest_under_the_trace_and_end(self) -> None:
        sdk = StubSdk()
        with _tracer(sdk).trace(_spec()) as scope, scope.span("retrieve", metadata={"k": 3}):
            pass

        (trace,) = sdk.of_kind("trace")
        (span,) = sdk.of_kind("span")
        assert span in trace.children, "an orphan span is invisible in the UI"
        assert span.payload["name"] == "retrieve"
        assert span.payload["metadata"] == {"k": 3}
        assert span.ends == [{}], "the context manager must close the span exactly once"

    def test_a_failing_node_leaves_an_error_span_not_a_missing_one(self) -> None:
        sdk = StubSdk()
        with (
            pytest.raises(RuntimeError),
            _tracer(sdk).trace(_spec()) as scope,
            scope.span("understand"),
        ):
            raise RuntimeError("provider exhausted")

        (span,) = sdk.of_kind("span")
        assert span.ends[0]["level"] == "ERROR"
        assert "provider exhausted" in span.ends[0]["status_message"]

    def test_a_generation_records_the_model_provider_and_token_usage(self) -> None:
        sdk = StubSdk()
        spec = GenerationSpec(
            name="understand", provider="gemini", model="", temperature=0.0, max_tokens=512
        )
        with _tracer(sdk).trace(_spec()) as scope, scope.generation(spec) as generation:
            generation.record_completion(
                provider="openrouter",
                model="llama-3.3-70b",
                input_tokens=120,
                output_tokens=42,
            )

        (generation_client,) = sdk.of_kind("generation")
        assert generation_client.payload["model_parameters"] == {
            "temperature": 0.0,
            "max_tokens": 512,
        }
        update = generation_client.updates[0]
        # The model is only knowable after the call — reporting it is this
        # method's whole reason to exist.
        assert update["model"] == "llama-3.3-70b"
        # FR-7.1: token usage per generation, or there is no cost analysis.
        assert update["usage"] == {"input": 120, "output": 42, "unit": "TOKENS"}
        # The chain rotated: the generation must name who *answered*, not the
        # provider we hoped for.
        assert update["metadata"]["provider"] == "openrouter"

    def test_a_stream_reports_no_usage_rather_than_zero(self) -> None:
        """Zeros would read as "this call was free" — an unknown must stay
        unknown (the delta contract carries no token counts)."""
        sdk = StubSdk()
        spec = GenerationSpec(name="generate_answer", provider="gemini", model="")
        with _tracer(sdk).trace(_spec()) as scope, scope.generation(spec) as generation:
            generation.record_completion(provider="gemini", model="gemini-2.0-flash")

        (generation_client,) = sdk.of_kind("generation")
        assert "usage" not in generation_client.updates[0]
        assert generation_client.updates[0]["model"] == "gemini-2.0-flash"


class TestMaskingBeforeExport:
    """NFR-1: the marker must never reach Langfuse unmasked."""

    def test_the_marker_never_leaves_in_a_trace_input(self) -> None:
        sdk = StubSdk()
        with _tracer(sdk).trace(_spec(input=f"transfere pra {CPF_MARKER}")):
            pass
        assert CPF_MARKER not in sdk.everything()
        assert "***.444.777-**" in sdk.everything()

    def test_the_marker_never_leaves_in_span_input_output_or_nested_metadata(self) -> None:
        sdk = StubSdk()
        with (
            _tracer(sdk).trace(_spec()) as scope,
            scope.span("retrieve", input={"query": CPF_MARKER}) as span,
        ):
            span.update(
                output=[f"contato: {EMAIL_MARKER}"],
                metadata={"nested": {"deep": CPF_MARKER}},
            )
        assert CPF_MARKER not in sdk.everything()
        assert EMAIL_MARKER not in sdk.everything()

    def test_the_marker_never_leaves_in_a_generation_prompt_or_completion(self) -> None:
        sdk = StubSdk()
        spec = GenerationSpec(
            name="understand",
            provider="fake",
            model="m",
            input=[{"role": "user", "content": f"meu cpf é {CPF_MARKER}"}],
        )
        with _tracer(sdk).trace(_spec()) as scope, scope.generation(spec) as generation:
            generation.update(output=f"confirmando {CPF_MARKER}")
        assert CPF_MARKER not in sdk.everything()

    def test_correlation_ids_are_not_masked(self) -> None:
        """They are UUID-shaped but not PII — masking them would defeat the one
        job the trace has (`masking.CORRELATION_KEYS`)."""
        sdk = StubSdk()
        correlation = "4f1c2b7e-2b4d-4a6f-9c1e-8a2b3c4d5e6f"
        with _tracer(sdk).trace(_spec()) as scope:
            scope.update(metadata={"correlation_id": correlation})
        assert correlation in sdk.everything()


class TestResilience:
    """PRD013-FR-6: Langfuse down ⇒ turns complete, one warning per interval."""

    def test_a_broken_sdk_never_reaches_the_caller(self) -> None:
        tracer = _tracer(StubSdk(fail=True))
        with tracer.trace(_spec()) as scope, scope.span("retrieve") as span:
            span.update(output="fine")
            with span.generation(GenerationSpec(name="g", provider="p", model="m")) as generation:
                generation.record_completion(
                    provider="p", model="m", input_tokens=1, output_tokens=1
                )
        tracer.flush()  # must not raise either

    def test_a_construction_failure_degrades_to_no_op(self, monkeypatch: MonkeyPatch) -> None:
        """The SDK client failing to build is the same story: no traces, but an
        app that still boots and still answers."""
        import langfuse

        logged = _record_warnings(monkeypatch)

        def explode(**_: Any) -> Any:
            raise RuntimeError("invalid key")

        monkeypatch.setattr(langfuse, "Langfuse", explode)

        tracer = LangfuseTracer(public_key="pk", secret_key="sk", host="http://langfuse:3000")
        with tracer.trace(_spec()) as scope, scope.span("understand"):
            pass
        tracer.flush()

        assert len(logged) == 1
        assert logged[0][0] == "telemetry.export_failed"

    def test_a_dead_tracer_does_not_swallow_the_caller_s_exception(self) -> None:
        """The tracer degrades; the turn's own failure still propagates."""
        tracer = _tracer(StubSdk(fail=True))
        with pytest.raises(ValueError, match="business failure"), tracer.trace(_spec()):
            raise ValueError("business failure")

    def test_warnings_are_rate_limited_to_one_per_interval(self, monkeypatch: MonkeyPatch) -> None:
        logged = _record_warnings(monkeypatch)
        warn = _WarnOnce(interval_seconds=60.0, now=lambda: 1000.0)

        for _ in range(50):
            warn(ConnectionError("down"))

        assert len(logged) == 1, "an outage must cost one line, not one per span"
        assert warn.suppressed == 49

    def test_the_warning_repeats_once_the_interval_passes(self, monkeypatch: MonkeyPatch) -> None:
        logged = _record_warnings(monkeypatch)
        clock = {"now": 1000.0}
        warn = _WarnOnce(interval_seconds=60.0, now=lambda: clock["now"])

        warn(ConnectionError("down"))
        clock["now"] += 61.0
        warn(ConnectionError("still down"))

        assert len(logged) == 2, "permanent silence would hide a permanent outage"
        assert logged[1][1]["suppressed_since_last"] == 0

    def test_an_outage_during_a_turn_warns_once_across_every_span(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        """The acceptance criterion, end to end at this layer: many failing SDK
        calls in one turn, one line in the log."""
        logged = _record_warnings(monkeypatch)
        tracer = _tracer(StubSdk(fail=True), warn=_WarnOnce(interval_seconds=60.0))

        with tracer.trace(_spec()) as scope:
            for node in ("input_guardrails", "understand", "retrieve", "output_guardrails"):
                with scope.span(node) as span:
                    span.update(output="x")

        assert len(logged) == 1


class TestSdkLogsAreTamed:
    """The SDK ships events from a background thread, so its failures never
    reach our try/except — it logs them itself, one ERROR per failed batch.
    Found by pointing a real tracer at a dead host: turns survived, but the log
    filled with `ERROR:langfuse:Unexpected error occurred`, which is both spam
    and the wrong level (telemetry.md §2: `error` means a broken request).
    """

    def test_sdk_records_are_rate_limited_through_our_warning(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        logged = _record_warnings(monkeypatch)
        _tracer(StubSdk(), warn=_WarnOnce(interval_seconds=60.0))

        sdk_logger = logging.getLogger("langfuse")
        for _ in range(20):
            sdk_logger.error("Unexpected error occurred.")

        assert len(logged) == 1, "one line per interval, not one per failed batch"
        assert logged[0][0] == "telemetry.export_failed"
        assert logged[0][1]["error_type"] == "SdkLog"

    def test_sdk_records_do_not_propagate_to_the_root_logger(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        """Otherwise the raw ERROR still reaches stdout beside our warning."""
        _record_warnings(monkeypatch)
        _tracer(StubSdk())
        assert logging.getLogger("langfuse").propagate is False

    def test_sdk_records_are_routed_not_silenced(self, monkeypatch: MonkeyPatch) -> None:
        """Silencing would make an outage invisible: our own calls only enqueue,
        so they succeed even while every export is failing."""
        logged = _record_warnings(monkeypatch)
        _tracer(StubSdk(), warn=_WarnOnce(interval_seconds=60.0))

        logging.getLogger("langfuse").warning("queue is full")

        assert len(logged) == 1
        assert "queue is full" in logged[0][1]["error"]


def _record_warnings(monkeypatch: MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Swap the adapter's logger for a recorder, undone after the test."""
    sink: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def warning(self, event: str, **kwargs: Any) -> None:
            sink.append((event, kwargs))

    monkeypatch.setattr(tracer_module, "logger", Recorder())
    return sink
