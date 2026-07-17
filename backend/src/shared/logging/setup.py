"""structlog configuration: JSON to stdout, correlation ids via contextvars,
PII masking in the pipeline (telemetry.md §2)."""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

from shared.logging.processors import mask_pii_processor
from shared.telemetry.correlation import set_trace_id, trace_context


def configure_logging(log_level: str = "INFO") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            mask_pii_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def bind_correlation(trace_id: str | None = None, request_id: str | None = None) -> None:
    """Bind correlation ids for every log line in the current context.

    `trace_id` also lands in `shared.telemetry.correlation`, which is what domain
    events read to stamp themselves (FR-7.2). Two contextvars rather than one
    because `domain/` may not import structlog — this function is the seam that
    keeps them in step, so bind here and nowhere else.
    """
    values: dict[str, str] = {}
    if trace_id is not None:
        values["trace_id"] = trace_id
        set_trace_id(trace_id)
    if request_id is not None:
        values["request_id"] = request_id
    if values:
        structlog.contextvars.bind_contextvars(**values)


def clear_correlation() -> None:
    structlog.contextvars.clear_contextvars()
    set_trace_id(None)


@contextmanager
def correlation_context(trace_id: str, request_id: str | None = None) -> Iterator[str]:
    """Bind correlation ids for the enclosed block, then unbind exactly those.

    Scoped rather than `bind` + `clear`: a turn must not wipe context ids it did
    not set (a request_id from the HTTP layer, say). Concurrent turns are
    unaffected either way — contextvars are per-task.
    """
    values = {"trace_id": trace_id} | ({"request_id": request_id} if request_id else {})
    structlog.contextvars.bind_contextvars(**values)
    try:
        with trace_context(trace_id):
            yield trace_id
    finally:
        structlog.contextvars.unbind_contextvars(*values)
