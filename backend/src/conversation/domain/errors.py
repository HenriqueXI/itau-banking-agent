"""Typed conversation failures (returned via Result, never raised)."""

from shared.domain.errors import DomainError


def llm_unavailable(detail: str = "all providers failed") -> DomainError:
    return DomainError(code="conversation.llm_unavailable", message=f"LLM unavailable: {detail}")


def knowledge_unavailable() -> DomainError:
    return DomainError(
        code="conversation.knowledge_unavailable", message="Knowledge base is unreachable"
    )


def thread_not_owned() -> DomainError:
    """Never distinguishes 'thread does not exist' from 'belongs to someone
    else' — an enumeration oracle is a leak (security.md)."""
    return DomainError(code="conversation.thread_not_owned", message="Thread is not accessible")


def understanding_failed() -> DomainError:
    return DomainError(
        code="conversation.understanding_failed",
        message="Could not extract a usable understanding from the model output",
    )
