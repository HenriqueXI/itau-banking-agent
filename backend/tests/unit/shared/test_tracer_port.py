"""The port's own contract: nesting, `current_scope`, and the no-op adapter.

The no-op tracer is not an afterthought — it is what every unit test in this
repo runs on (NFR-7), so "does nothing" must still mean "nests correctly".
A no-op that skipped the contextvar would leave the traced code path untested
everywhere else.
"""

from shared.adapters.noop_tracer import NoopTracer
from shared.application.ports.tracer import (
    GenerationSpec,
    TraceSpec,
    annotate,
    current_scope,
)
from tests.fakes import RecordingTracer


def _spec(name: str = "turn") -> TraceSpec:
    return TraceSpec(name=name, trace_id="tr-1", session_id="t-1", user_id="u-1")


class TestScopePropagation:
    def test_no_scope_is_current_outside_a_trace(self) -> None:
        assert current_scope() is None

    def test_the_open_scope_is_current_inside_a_trace(self) -> None:
        tracer = NoopTracer()
        with tracer.trace(_spec()) as scope:
            assert current_scope() is scope

    def test_the_innermost_span_wins_and_the_parent_is_restored(self) -> None:
        """This is what lets `TracedLlm` name a generation after the node it ran
        in without anyone passing the node's name down to it."""
        with NoopTracer().trace(_spec()) as trace:
            with trace.span("understand"):
                assert _current_name() == "understand"
            assert _current_name() == "turn"

    def test_the_scope_is_released_when_the_trace_closes(self) -> None:
        with NoopTracer().trace(_spec()):
            pass
        assert current_scope() is None

    def test_the_scope_is_released_when_the_block_raises(self) -> None:
        """Otherwise one failed turn poisons every later turn on the task with a
        stale parent — the kind of bug that only shows up in production."""
        try:
            with NoopTracer().trace(_spec()):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert current_scope() is None


class TestAnnotate:
    def test_annotate_is_a_no_op_without_a_trace(self) -> None:
        annotate(anything="at all")  # must not raise

    def test_annotate_writes_to_the_innermost_span(self) -> None:
        tracer = RecordingTracer()
        with tracer.trace(_spec()) as trace, trace.span("retrieve"):
            annotate(best_score=0.9)
        assert tracer.named("retrieve").metadata["best_score"] == 0.9
        assert "best_score" not in tracer.named("turn").metadata


class TestRecordingTracerNesting:
    def test_spans_and_generations_nest_under_the_trace(self) -> None:
        tracer = RecordingTracer()
        spec = GenerationSpec(name="understand", provider="fake", model="m")
        with (
            tracer.trace(_spec()) as scope,
            scope.span("understand") as span,
            span.generation(spec) as generation,
        ):
            generation.record_completion(
                provider="fake", model="m", input_tokens=11, output_tokens=7
            )

        assert tracer.paths() == ["turn", "turn/understand", "turn/understand/understand"]
        assert tracer.generations()[0].usage == (11, 7)


def _current_name() -> str:
    scope = current_scope()
    assert scope is not None, "expected an open scope"
    return scope.name
